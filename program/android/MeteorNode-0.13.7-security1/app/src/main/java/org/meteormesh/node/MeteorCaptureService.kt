package org.meteormesh.node

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.hardware.camera2.CameraAccessException
import android.hardware.camera2.CameraCaptureSession
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CameraDevice
import android.hardware.camera2.CameraManager
import android.hardware.camera2.CaptureRequest
import android.hardware.camera2.TotalCaptureResult
import android.media.MediaCodec
import android.media.MediaCodecInfo
import android.media.MediaFormat
import android.media.MediaMuxer
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.HandlerThread
import android.os.IBinder
import android.os.PowerManager
import android.os.Looper
import android.os.SystemClock
import android.util.Range
import android.util.Size
import android.view.Surface
import java.io.File

/**
 * MeteorNode camera service. One Camera2 session and encoder stay active across segment boundaries.
 * MP4 muxers rotate on encoder presentation timestamps, with a size ceiling as a safety bound.
 * Remote commands are accepted only while the user already has a foreground camera service.
 * No completed-video automatic deletion or Android background-camera permission bypass is implemented.
 */
class MeteorCaptureService : Service() {
    private lateinit var cameraThread: HandlerThread
    private lateinit var cameraHandler: Handler
    private lateinit var store: SegmentStore
    private var wakeLock: PowerManager.WakeLock? = null
    private var cameraDevice: CameraDevice? = null
    private var session: CameraCaptureSession? = null
    private var encoder: MediaCodec? = null
    private var encoderInputSurface: Surface? = null
    private var muxer: MediaMuxer? = null
    private var encoderOutputFormat: MediaFormat? = null
    private var muxerTrackIndex = -1
    private var active: ActiveSegment? = null
    private var activeExpectedSegmentBytes = 0L
    private var activeEmergencyFileLimitBytes = 0L
    private var segmentStartPtsUs: Long? = null
    private var streamStartPtsUs: Long? = null
    private var streamStartUtcMs: Long? = null
    private var streamStartMonotonicNs: Long? = null
    private var lastSegmentPtsUs: Long? = null
    private var bytesInSegment = 0L
    private var encodedSampleCount = 0L
    private var rotationRequested = false
    private var rotationRequestReason = ""
    private var encoderFinishing = false
    private var encoderDrainScheduled = false
    private var destroying = false
    private val encoderBufferInfo = MediaCodec.BufferInfo()
    private val encoderDrainRunnable = Runnable {
        encoderDrainScheduled = false
        drainEncoder()
    }
    private val encoderStopTimeoutRunnable = Runnable {
        if (encoderFinishing && encoder != null) {
            saveError("Encoder EOS did not arrive before the stop timeout; partial recording retained for inspection")
            abortEncoder()
            completeStoppedRecording()
        }
    }
    private var recording = false
    private var starting = false
    private var segmentStarting = false
    @Volatile private var desiredRecording = false
    private var selectedProfile = "auto"
    @Volatile private var remoteForegroundReady = false
    private var cameraId = ""
    private var sensorClock = "UNKNOWN"
    private var firstSensorTimestampNs: Long? = null
    private var lastSensorTimestampNs: Long? = null
    private var captureResultCount = 0L
    private var rolloutRetries = 0
    private var cameraOpenRetries = 0
    private var cameraRetryPending = false
    private var cameraRetryAfterFinalize = false

    override fun onCreate() {
        super.onCreate()
        cameraThread = HandlerThread("meteor-camera").apply { start() }
        cameraHandler = Handler(cameraThread.looper)
        store = SegmentStore.get(this)
        instance = this
        saveState("idle")
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_ARM -> {
                // Invoked only from a visible Activity after runtime CAMERA permission approval.
                // A remote HTTP request never creates a new camera foreground service.
                if (checkSelfPermission(Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED) {
                    saveError("Camera permission denied; cannot arm remote control")
                    stopSelf()
                    return START_NOT_STICKY
                }
                try {
                    startForegroundRecording()
                    remoteForegroundReady = true
                    if (!desiredRecording) saveState("armed_idle")
                    RemoteSettings.noteCommand(this, "armed_on_phone")
                } catch (e: Exception) {
                    remoteForegroundReady = false
                    saveError("Cannot arm camera foreground service: ${e.javaClass.simpleName}: ${e.message}")
                    stopSelf()
                }
            }
            ACTION_START -> {
                if (desiredRecording) return START_NOT_STICKY
                if (checkSelfPermission(Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED) {
                    saveError("Camera permission denied")
                    stopSelf()
                    return START_NOT_STICKY
                }
                desiredRecording = true
                selectedProfile = intent.getStringExtra(EXTRA_PROFILE) ?: RemoteSettings.profile(this)
                try {
                    startForegroundRecording()
                    remoteForegroundReady = true
                } catch (e: Exception) {
                    desiredRecording = false
                    saveError("Camera foreground service unavailable: ${e.message}")
                    stopSelf()
                    return START_NOT_STICKY
                }
                cameraHandler.post { startSession() }
            }
            ACTION_STOP -> {
                desiredRecording = false
                cameraHandler.post { stopSegment() }
            }
        }
        return START_NOT_STICKY
    }

    private fun startForegroundRecording() {
        val manager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        manager.createNotificationChannel(NotificationChannel(
            CHANNEL_ID, "Meteor kamera kaydı", NotificationManager.IMPORTANCE_LOW
        ))
        val reopen = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        val notification = Notification.Builder(this, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.ic_menu_camera)
            .setContentTitle("MeteorNode")
            .setContentText("Kamera servisi hazir; kayit durumunu MeteorNode uygulamasindan gorun")
            .setContentIntent(reopen)
            .setOngoing(true)
            .build()
        if (Build.VERSION.SDK_INT >= 34) {
            startForeground(NOTIFICATION_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_CAMERA)
        } else {
            startForeground(NOTIFICATION_ID, notification)
        }
    }

    private fun startSession() {
        if (!desiredRecording || recording || starting || cameraDevice != null) return
        starting = true
        try {
            // Android permissions are revocable even after the UI's start check.
            // Check again on the camera worker immediately before openCamera().
            if (checkSelfPermission(Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED) {
                starting = false
                desiredRecording = false
                stopSegment()
                saveError("Camera permission revoked; recording stopped safely")
                return
            }
            val manager = getSystemService(Context.CAMERA_SERVICE) as CameraManager
            cameraId = CameraInventory.backCameraId(this)
            val char = manager.getCameraCharacteristics(cameraId)
            sensorClock = if (char.get(CameraCharacteristics.SENSOR_INFO_TIMESTAMP_SOURCE) ==
                CameraCharacteristics.SENSOR_INFO_TIMESTAMP_SOURCE_REALTIME) "REALTIME" else "UNKNOWN"
            manager.openCamera(cameraId, object : CameraDevice.StateCallback() {
                override fun onOpened(device: CameraDevice) {
                    if (!desiredRecording) { device.close(); starting = false; return }
                    cameraDevice = device
                    starting = false
                    acquireWakeLock()
                    startSegment()
                }
                override fun onDisconnected(device: CameraDevice) {
                    device.close()
                    cameraDevice = null
                    starting = false
                    recoverCamera("Camera disconnected")
                }
                override fun onError(device: CameraDevice, error: Int) {
                    device.close()
                    cameraDevice = null
                    starting = false
                    recoverCamera("CameraDevice error=$error")
                }
            }, cameraHandler)
        } catch (e: Exception) {
            starting = false
            recoverCamera("openCamera: ${e.message}")
        }
    }

    private fun recoverCamera(reason: String) {
        // Camera2 errors may be transient. Never keep desiredRecording=true with no retry.
        saveError(reason)
        if (cameraRetryPending || cameraRetryAfterFinalize) return
        cameraRetryAfterFinalize = true
        stopSegment()
    }

    private fun scheduleCameraRetry() {
        if (!desiredRecording || cameraRetryPending) return
        if (cameraOpenRetries >= MAX_RETRIES) {
            cameraRetryAfterFinalize = false
            desiredRecording = false
            saveError("Camera recovery exhausted ($MAX_RETRIES)")
            return
        }
        cameraOpenRetries++
        cameraRetryPending = true
        saveState("retrying_camera:$cameraOpenRetries")
        cameraHandler.postDelayed({
            cameraRetryPending = false
            if (desiredRecording) startSession()
        }, RETRY_DELAY_MS * cameraOpenRetries)
    }

    private fun recoverSegment(reason: String) {
        saveError(reason)
        try { session?.close() } catch (_: Exception) { }
        session = null
        abortEncoder()
        active = null
        recording = false
        activeExpectedSegmentBytes = 0L
        activeEmergencyFileLimitBytes = 0L
        segmentStarting = false
        if (!desiredRecording) {
            stopSegment()
        } else if (cameraDevice != null && rolloutRetries < MAX_RETRIES) {
            rolloutRetries++
            saveState("retrying_segment:$rolloutRetries")
            cameraHandler.postDelayed({ startSegment() }, RETRY_DELAY_MS * rolloutRetries)
        } else {
            desiredRecording = false
            saveError("Segment recovery exhausted ($MAX_RETRIES): $reason")
            stopSegment()
        }
    }

    private fun startSegment() {
        if (!desiredRecording) return
        val camera = cameraDevice ?: return
        if (recording || segmentStarting) return
        if (encoder != null) {
            if (encoderFinishing) cameraHandler.postDelayed({ startSegment() }, ENCODER_STOP_RETRY_MS)
            return
        }
        segmentStarting = true
        try {
            // Until verified auto-retention exists, low storage must stop safely.
            val segmentDurationMs = RemoteSettings.segmentMs(this)
            val bitrate = RemoteSettings.bitrate(this)
            val expectedSegmentBytes = RecordingPolicy.expectedSegmentBytes(bitrate, segmentDurationMs)
            val emergencyFileLimitBytes = RecordingPolicy.emergencyFileLimitBytes(expectedSegmentBytes)
            if (store.freeBytes() < RecordingPolicy.requiredFreeBytes(emergencyFileLimitBytes)) {
                segmentStarting = false
                desiredRecording = false
                saveError("Protected 32 GiB storage reserve or segment headroom would be breached; originals retained")
                stopSegment()
                saveState("stopped_low_storage")
                saveRotationStatus("stopped_low_storage", null, null)
                return
            }
            val char = CameraInventory.cameraCharacteristics(this, cameraId)
            val size = CameraInventory.videoSize(char)
            val (fps, range) = CameraInventory.fps(char)
            val requested = RemoteSettings.profile(this)
            val manual = requested != "auto" && CameraInventory.supportsManual(char)
            val effective = if (manual) requested else "auto"
            if (requested != "auto" && !manual) saveError("MANUAL_SENSOR unsupported; fell back to AUTO")

            val next = store.create(
                cameraId, effective, size.width, size.height, fps, bitrate,
                segmentDurationMs, "CAMERA_SESSION_START"
            )
            val videoFormat = MediaFormat.createVideoFormat(
                MediaFormat.MIMETYPE_VIDEO_AVC, size.width, size.height
            ).apply {
                setInteger(MediaFormat.KEY_COLOR_FORMAT, MediaCodecInfo.CodecCapabilities.COLOR_FormatSurface)
                setInteger(MediaFormat.KEY_BIT_RATE, bitrate)
                setInteger(MediaFormat.KEY_FRAME_RATE, fps)
                // Frequent sync frames bound the wait between a time target and the next MP4 boundary.
                setInteger(MediaFormat.KEY_I_FRAME_INTERVAL, 1)
            }
            val videoEncoder = MediaCodec.createEncoderByType(MediaFormat.MIMETYPE_VIDEO_AVC)
            try {
                videoEncoder.configure(videoFormat, null, null, MediaCodec.CONFIGURE_FLAG_ENCODE)
            } catch (e: Exception) {
                try { videoEncoder.release() } catch (_: Exception) { }
                throw e
            }
            val inputSurface = try {
                videoEncoder.createInputSurface()
            } catch (e: Exception) {
                try { videoEncoder.release() } catch (_: Exception) { }
                throw e
            }
            encoder = videoEncoder
            encoderInputSurface = inputSurface
            encoderOutputFormat = null
            muxerTrackIndex = -1
            muxer = null
            active = next.copy(
                startedUtcMs = System.currentTimeMillis(),
                startedMonotonicNs = SystemClock.elapsedRealtimeNanos()
            )
            activeExpectedSegmentBytes = expectedSegmentBytes
            activeEmergencyFileLimitBytes = emergencyFileLimitBytes
            resetSegmentCounters()
            firstSensorTimestampNs = null
            lastSensorTimestampNs = null
            captureResultCount = 0
            encoderFinishing = false
            videoEncoder.start()
            scheduleEncoderDrain()

            camera.createCaptureSession(
                listOf(inputSurface),
                object : CameraCaptureSession.StateCallback() {
                    override fun onConfigured(configured: CameraCaptureSession) {
                        segmentStarting = false
                        if (cameraDevice == null || !desiredRecording) {
                            configured.close()
                            if (encoderFinishing) {
                                scheduleEncoderDrain()
                            } else {
                                abortEncoder()
                                active = null
                                completeStoppedRecording()
                            }
                            return
                        }
                        session = configured
                        try {
                            val request = camera.createCaptureRequest(CameraDevice.TEMPLATE_RECORD).apply {
                                addTarget(inputSurface)
                                applySettings(this, char, manual, requested, range)
                            }.build()
                            configured.setRepeatingRequest(request, object : CameraCaptureSession.CaptureCallback() {
                                override fun onCaptureCompleted(
                                    session: CameraCaptureSession,
                                    request: CaptureRequest,
                                    result: TotalCaptureResult
                                ) {
                                    val ts = result.get(android.hardware.camera2.CaptureResult.SENSOR_TIMESTAMP)
                                    ts?.let { noteSensorTimestamp(it) }
                                }
                            }, cameraHandler)
                            recording = true
                            rolloutRetries = 0
                            cameraOpenRetries = 0
                            saveState("recording:${next.id}")
                            saveRotationStatus("timed_muxer_waiting_for_sync_frame", active?.id, null)
                            if (RemoteSettings.lastCommand(this@MeteorCaptureService) == "remote_start_queued") {
                                RemoteSettings.noteCommand(this@MeteorCaptureService, "remote_start_recording")
                            }
                        } catch (e: Exception) {
                            recoverSegment("start: ${e.message}")
                        }
                    }
                    override fun onConfigureFailed(failed: CameraCaptureSession) {
                        segmentStarting = false
                        failed.close()
                        recoverSegment("Camera capture session configuration failed")
                    }
                }, cameraHandler
            )
        } catch (e: Exception) {
            segmentStarting = false
            recoverSegment("startSegment: ${e.javaClass.simpleName} ${e.message}")
        }
    }

    private fun scheduleEncoderDrain(delayMs: Long = 0L) {
        if (encoder == null || encoderDrainScheduled) return
        encoderDrainScheduled = true
        cameraHandler.postDelayed(encoderDrainRunnable, delayMs)
    }

    /** Drain encoded samples without blocking Camera2 callbacks; only the muxer changes at rollover. */
    private fun drainEncoder() {
        val codec = encoder ?: return
        try {
            while (true) {
                val index = codec.dequeueOutputBuffer(encoderBufferInfo, 0)
                when {
                    index == MediaCodec.INFO_TRY_AGAIN_LATER -> break
                    index == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED -> onEncoderOutputFormat(codec.outputFormat)
                    index == MediaCodec.INFO_OUTPUT_BUFFERS_CHANGED -> Unit
                    index >= 0 -> {
                        val endOfStream = (encoderBufferInfo.flags and MediaCodec.BUFFER_FLAG_END_OF_STREAM) != 0
                        try {
                            if (encoderBufferInfo.size > 0) processEncodedOutput(codec, index, encoderBufferInfo)
                        } finally {
                            try { codec.releaseOutputBuffer(index, false) } catch (_: Exception) { }
                        }
                        if (endOfStream) {
                            onEncoderEndOfStream()
                            return
                        }
                    }
                }
            }
        } catch (e: Exception) {
            if (encoderFinishing) {
                saveError("Encoder finalization failed; current recording retained for inspection: ${e.message}")
                abortEncoder()
                completeStoppedRecording()
            } else {
                recoverSegment("MediaCodec drain failed: ${e.javaClass.simpleName}: ${e.message}")
            }
            return
        }
        if (encoder != null) scheduleEncoderDrain(10L)
    }

    private fun onEncoderOutputFormat(format: MediaFormat) {
        if (encoderOutputFormat != null) {
            failRotation("MediaCodec output format changed mid-recording; current data retained")
            return
        }
        encoderOutputFormat = format
        val current = active ?: run {
            failRotation("Encoder produced a format without an active segment")
            return
        }
        try {
            val prepared = createMuxer(current, format)
            muxer = prepared.first
            muxerTrackIndex = prepared.second
            saveRotationStatus("timed_muxer_waiting_for_sync_frame", current.id, null)
        } catch (e: Exception) {
            failRotation("Could not start MP4 muxer: ${e.javaClass.simpleName}: ${e.message}")
        }
    }

    private fun createMuxer(segment: ActiveSegment, format: MediaFormat): Pair<MediaMuxer, Int> {
        val output = MediaMuxer(segment.partialFile.absolutePath, MediaMuxer.OutputFormat.MUXER_OUTPUT_MPEG_4)
        try {
            output.setOrientationHint(0)
            val track = output.addTrack(format)
            output.start()
            return output to track
        } catch (e: Exception) {
            try { output.release() } catch (_: Exception) { }
            throw e
        }
    }

    private fun processEncodedOutput(codec: MediaCodec, index: Int, info: MediaCodec.BufferInfo) {
        if ((info.flags and MediaCodec.BUFFER_FLAG_CODEC_CONFIG) != 0) return
        val format = encoderOutputFormat ?: error("Encoder output arrived before its format")
        if (muxer == null) {
            val current = active ?: error("Encoder output arrived without an active segment")
            val prepared = createMuxer(current, format)
            muxer = prepared.first
            muxerTrackIndex = prepared.second
        }
        val payload = codec.getOutputBuffer(index) ?: return
        val ptsUs = info.presentationTimeUs
        val isSyncFrame = (info.flags and MediaCodec.BUFFER_FLAG_KEY_FRAME) != 0

        if (segmentStartPtsUs == null) {
            // An MP4 must begin with a decodable sync frame. Keep earlier dependent frames out.
            if (!isSyncFrame) return
            val utc = System.currentTimeMillis()
            val monotonic = SystemClock.elapsedRealtimeNanos()
            streamStartPtsUs = ptsUs
            streamStartUtcMs = utc
            streamStartMonotonicNs = monotonic
            segmentStartPtsUs = ptsUs
            active = active?.copy(startedUtcMs = utc, startedMonotonicNs = monotonic)
            saveRotationStatus("timed_muxer_active", active?.id, null)
        }

        val current = active ?: error("No active segment for encoded sample")
        val segmentStart = segmentStartPtsUs ?: error("Missing segment presentation timestamp")
        val previousPtsUs = lastSegmentPtsUs
        if (previousPtsUs != null && ptsUs <= previousPtsUs) {
            failRotation("Encoder presentation timestamps are not strictly increasing; segment retained for inspection")
            return
        }
        val durationReached = ptsUs - segmentStart >= current.targetSegmentMs * 1_000L
        val emergencySizeTrigger = bytesInSegment + info.size >= activeEmergencyFileLimitBytes
        if (!encoderFinishing && !rotationRequested && (durationReached || emergencySizeTrigger)) {
            rotationRequested = true
            rotationRequestReason = if (durationReached) "duration" else "emergency_size"
            saveRotationStatus("timed_muxer_rotation_requested_$rotationRequestReason", current.id, null)
            try {
                encoder?.setParameters(Bundle().apply {
                    putInt(MediaCodec.PARAMETER_KEY_REQUEST_SYNC_FRAME, 0)
                })
            } catch (e: Exception) {
                // The encoder is configured for one-second GOPs; use the next natural sync frame.
                saveError("Sync-frame request unavailable; waiting for encoder keyframe: ${e.message}")
            }
        }

        if (!encoderFinishing && rotationRequested && isSyncFrame && encodedSampleCount > 0L) {
            rotateAtSyncFrame(ptsUs)
        }

        val currentMuxer = muxer ?: error("MP4 muxer unavailable for encoded sample")
        val relativePts = (ptsUs - (segmentStartPtsUs ?: ptsUs)).coerceAtLeast(0L)
        val source = payload.duplicate().apply {
            position(info.offset)
            limit(info.offset + info.size)
        }
        val sample = source.slice()
        val sampleInfo = MediaCodec.BufferInfo().apply {
            set(0, info.size, relativePts, info.flags)
        }
        currentMuxer.writeSampleData(muxerTrackIndex, sample, sampleInfo)
        bytesInSegment += info.size
        encodedSampleCount++
        lastSegmentPtsUs = ptsUs
    }

    private fun rotateAtSyncFrame(boundaryPtsUs: Long) {
        val previous = active ?: run {
            failRotation("No active segment at timed MP4 boundary")
            return
        }
        val outputFormat = encoderOutputFormat ?: run {
            failRotation("Encoder format unavailable at timed MP4 boundary")
            return
        }
        if (store.freeBytes() < RecordingPolicy.requiredFreeBytes(activeEmergencyFileLimitBytes)) {
            // Stop the current valid MP4 cleanly; never cross the protected reserve to create another.
            failRotation("Protected 32 GiB reserve or next-segment headroom reached; stopping safely")
            return
        }

        val streamOriginPts = streamStartPtsUs ?: boundaryPtsUs
        val deltaUs = (boundaryPtsUs - streamOriginPts).coerceAtLeast(0L)
        val boundaryUtcMs = (streamStartUtcMs ?: System.currentTimeMillis()) + deltaUs / 1_000L
        val boundaryMonoNs = (streamStartMonotonicNs ?: SystemClock.elapsedRealtimeNanos()) + deltaUs * 1_000L
        val nextBase = try {
            store.create(
                previous.cameraId, previous.mode, previous.width, previous.height, previous.fps,
                previous.bitrate, previous.targetSegmentMs, "MEDIACODEC_SYNC_FRAME"
            )
        } catch (e: Exception) {
            failRotation("Could not reserve next MP4: ${e.javaClass.simpleName}: ${e.message}")
            return
        }
        val next = nextBase.copy(
            startedUtcMs = boundaryUtcMs,
            startedMonotonicNs = boundaryMonoNs,
            boundaryStart = "MEDIACODEC_SYNC_FRAME"
        )
        val preparedNext = try {
            createMuxer(next, outputFormat)
        } catch (e: Exception) {
            failRotation("Could not prepare next MP4 muxer: ${e.javaClass.simpleName}: ${e.message}")
            return
        }

        val previousMuxer = muxer ?: run {
            try { preparedNext.first.release() } catch (_: Exception) { }
            failRotation("Current MP4 muxer missing at segment boundary")
            return
        }
        val previousStartPts = segmentStartPtsUs
        val previousEndPts = lastSegmentPtsUs
        val previousSampleCount = encodedSampleCount
        val completedSegmentation = when (rotationRequestReason) {
            "duration" -> "MEDIACODEC_PTS_DURATION_SYNC_FRAME"
            "emergency_size" -> "MEDIACODEC_EMERGENCY_SIZE_SYNC_FRAME"
            else -> "MEDIACODEC_PTS_SYNC_FRAME_UNKNOWN_TRIGGER"
        }
        try {
            previousMuxer.stop()
            previousMuxer.release()
        } catch (e: Exception) {
            try { preparedNext.first.release() } catch (_: Exception) { }
            failRotation("Could not close previous MP4: ${e.javaClass.simpleName}: ${e.message}")
            return
        }
        store.finalizeAsync(
            previous, firstSensorTimestampNs, lastSensorTimestampNs,
            captureResultCount, sensorClock, boundaryUtcMs,
            completedSegmentation, previousStartPts, previousEndPts, previousSampleCount
        )

        active = next
        muxer = preparedNext.first
        muxerTrackIndex = preparedNext.second
        segmentStartPtsUs = boundaryPtsUs
        lastSegmentPtsUs = null
        bytesInSegment = 0L
        encodedSampleCount = 0L
        rotationRequested = false
        rotationRequestReason = ""
        firstSensorTimestampNs = null
        lastSensorTimestampNs = null
        captureResultCount = 0L
        saveState("recording:${next.id}")
        saveRotationStatus("timed_muxer_active", next.id, null, boundaryUtcMs)
    }

    private fun noteSensorTimestamp(timestampNs: Long) {
        if (firstSensorTimestampNs == null) firstSensorTimestampNs = timestampNs
        lastSensorTimestampNs = timestampNs
        captureResultCount++
    }

    private fun resetSegmentCounters() {
        segmentStartPtsUs = null
        streamStartPtsUs = null
        streamStartUtcMs = null
        streamStartMonotonicNs = null
        lastSegmentPtsUs = null
        bytesInSegment = 0L
        encodedSampleCount = 0L
        rotationRequested = false
        rotationRequestReason = ""
    }

    private fun finalizeCurrentMuxer(endUtcMs: Long = System.currentTimeMillis()) {
        val current = active
        val currentMuxer = muxer
        if (currentMuxer != null) {
            currentMuxer.stop()
            currentMuxer.release()
            muxer = null
            muxerTrackIndex = -1
            if (current != null && encodedSampleCount > 0L) {
                store.finalizeAsync(
                    current, firstSensorTimestampNs, lastSensorTimestampNs,
                    captureResultCount, sensorClock, endUtcMs,
                    "MEDIACODEC_FINALIZE_STOP", segmentStartPtsUs, lastSegmentPtsUs, encodedSampleCount
                )
                active = null
            }
        }
    }

    private fun onEncoderEndOfStream() {
        cameraHandler.removeCallbacks(encoderStopTimeoutRunnable)
        if (!encoderFinishing) {
            recoverSegment("MediaCodec ended unexpectedly")
            return
        }
        try {
            finalizeCurrentMuxer()
        } catch (e: Exception) {
            saveError("MP4 finalization failed; recording retained for inspection: ${e.message}")
            try { muxer?.release() } catch (_: Exception) { }
            muxer = null
        }
        releaseEncoder()
        completeStoppedRecording()
    }

    /** Stop a failed encoder without publishing a partial or unverifiable MP4 as READY. */
    private fun abortEncoder() {
        cameraHandler.removeCallbacks(encoderDrainRunnable)
        cameraHandler.removeCallbacks(encoderStopTimeoutRunnable)
        encoderDrainScheduled = false
        try { muxer?.release() } catch (_: Exception) { }
        muxer = null
        muxerTrackIndex = -1
        releaseEncoder()
        encoderOutputFormat = null
        encoderFinishing = false
        resetSegmentCounters()
    }

    private fun releaseEncoder() {
        val currentEncoder = encoder
        encoder = null
        try { currentEncoder?.stop() } catch (_: Exception) { }
        try { currentEncoder?.release() } catch (_: Exception) { }
        try { encoderInputSurface?.release() } catch (_: Exception) { }
        encoderInputSurface = null
        encoderOutputFormat = null
        encoderDrainScheduled = false
        encoderFinishing = false
    }

    private fun completeStoppedRecording() {
        active = null
        segmentStarting = false
        recording = false
        activeExpectedSegmentBytes = 0L
        activeEmergencyFileLimitBytes = 0L
        try { cameraDevice?.close() } catch (_: Exception) { }
        cameraDevice = null
        releaseWakeLock()
        saveState("idle")
        saveRotationStatus("idle", null, null)
        if (RemoteSettings.lastCommand(this) == "remote_stop_queued") {
            RemoteSettings.noteCommand(this, "remote_stop_complete")
        }
        if (destroying) cameraThread.quitSafely()
        if (cameraRetryAfterFinalize && desiredRecording && !destroying) {
            cameraRetryAfterFinalize = false
            scheduleCameraRetry()
        } else if (desiredRecording && !destroying) {
            cameraHandler.post { startSession() }
        }
    }

    private fun failRotation(reason: String) {
        val failedSegmentId = active?.id
        desiredRecording = false
        saveError(reason)
        stopSegment()
        saveState("stopped_rotation_error")
        saveRotationStatus("stopped_error", failedSegmentId, null)
    }

    private fun saveRotationStatus(
        state: String,
        activeId: String?,
        nextId: String?,
        lastSwitchUtcMs: Long? = null
    ) {
        val prefs = getSharedPreferences("meteor-state", MODE_PRIVATE)
        prefs.edit()
            .putString("recording_rotation_state", state)
            .putString("recording_active_segment_id", activeId ?: "")
            .putString("recording_next_segment_id", nextId ?: "")
            .putLong("recording_segment_target_ms", active?.targetSegmentMs ?: RemoteSettings.segmentMs(this))
            .apply {
                // Preserve the old field as an alias for clients that only know one ceiling.
                putLong("recording_segment_file_limit_bytes", activeEmergencyFileLimitBytes)
                putLong("recording_segment_expected_bytes", activeExpectedSegmentBytes)
                putLong("recording_segment_emergency_limit_bytes", activeEmergencyFileLimitBytes)
                putLong("recording_storage_reserve_bytes", RecordingPolicy.PROTECTED_RESERVE_BYTES)
                putLong("recording_free_bytes_snapshot", store.freeBytes())
                if (lastSwitchUtcMs != null) putLong("recording_last_switch_utc_ms", lastSwitchUtcMs)
            }
            .apply()
    }

    private fun applySettings(
        builder: CaptureRequest.Builder,
        c: CameraCharacteristics,
        manual: Boolean,
        requested: String,
        fpsRange: Range<Int>?
    ) {
        fpsRange?.let { builder.set(CaptureRequest.CONTROL_AE_TARGET_FPS_RANGE, it) }
        // Fixed mount: begin with EIS/OIS off; compare OIS against stars on each phone.
        if (c.get(CameraCharacteristics.CONTROL_AVAILABLE_VIDEO_STABILIZATION_MODES)
                ?.contains(CaptureRequest.CONTROL_VIDEO_STABILIZATION_MODE_OFF) == true) {
            builder.set(CaptureRequest.CONTROL_VIDEO_STABILIZATION_MODE,
                CaptureRequest.CONTROL_VIDEO_STABILIZATION_MODE_OFF)
        }
        if (c.get(CameraCharacteristics.LENS_INFO_AVAILABLE_OPTICAL_STABILIZATION)
                ?.contains(CaptureRequest.LENS_OPTICAL_STABILIZATION_MODE_OFF) == true) {
            builder.set(CaptureRequest.LENS_OPTICAL_STABILIZATION_MODE,
                CaptureRequest.LENS_OPTICAL_STABILIZATION_MODE_OFF)
        }
        builder.set(CaptureRequest.CONTROL_AWB_MODE, CaptureRequest.CONTROL_AWB_MODE_AUTO)
        if (manual) {
            val requestedIso = if (requested == "manual_1600") 1600 else 800
            val isoRange = c.get(CameraCharacteristics.SENSOR_INFO_SENSITIVITY_RANGE)
            val exposureRange = c.get(CameraCharacteristics.SENSOR_INFO_EXPOSURE_TIME_RANGE)
            val frameRate = fpsRange?.upper ?: 25
            val requestedExposureNs = 1_000_000_000L / frameRate
            builder.set(CaptureRequest.CONTROL_AE_MODE, CaptureRequest.CONTROL_AE_MODE_OFF)
            builder.set(CaptureRequest.SENSOR_SENSITIVITY,
                isoRange?.clamp(requestedIso) ?: requestedIso)
            builder.set(CaptureRequest.SENSOR_EXPOSURE_TIME,
                exposureRange?.clamp(requestedExposureNs) ?: requestedExposureNs)
        } else {
            builder.set(CaptureRequest.CONTROL_AE_MODE, CaptureRequest.CONTROL_AE_MODE_ON)
        }
        val minimumFocusDistance = c.get(CameraCharacteristics.LENS_INFO_MINIMUM_FOCUS_DISTANCE)
        if (minimumFocusDistance != null && minimumFocusDistance > 0f) {
            builder.set(CaptureRequest.CONTROL_AF_MODE, CaptureRequest.CONTROL_AF_MODE_OFF)
            builder.set(CaptureRequest.LENS_FOCUS_DISTANCE, 0f)  // Infinity: verify with stars on each phone.
        } else {
            builder.set(CaptureRequest.CONTROL_AF_MODE, CaptureRequest.CONTROL_AF_MODE_CONTINUOUS_VIDEO)
        }
    }

    private fun stopSegment() {
        segmentStarting = false
        recording = false
        try { session?.stopRepeating() } catch (_: Exception) { }
        try { session?.close() } catch (_: Exception) { }
        session = null
        val currentEncoder = encoder
        if (currentEncoder != null) {
            if (encoderFinishing) {
                scheduleEncoderDrain(10L)
                return
            }
            encoderFinishing = true
            saveState("finalizing_mp4")
            try {
                // Closing the Camera2 input first lets EOS flush every queued encoder sample.
                currentEncoder.signalEndOfInputStream()
                cameraHandler.removeCallbacks(encoderStopTimeoutRunnable)
                cameraHandler.postDelayed(encoderStopTimeoutRunnable, ENCODER_STOP_TIMEOUT_MS)
                scheduleEncoderDrain()
            } catch (e: Exception) {
                saveError("Could not flush encoder EOS; current recording retained: ${e.message}")
                abortEncoder()
                completeStoppedRecording()
            }
            return
        }
        completeStoppedRecording()
    }

    private fun acquireWakeLock() {
        if (wakeLock?.isHeld == true) return
        val power = getSystemService(Context.POWER_SERVICE) as PowerManager
        wakeLock = power.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "MeteorNode:Recording").apply {
            setReferenceCounted(false)
            acquire()
        }
    }

    private fun releaseWakeLock() {
        try { if (wakeLock?.isHeld == true) wakeLock?.release() } catch (_: Exception) { }
    }

    private fun saveState(value: String) {
        getSharedPreferences("meteor-state", MODE_PRIVATE).edit().putString("state", value).apply()
    }

    private fun saveError(value: String) {
        getSharedPreferences("meteor-state", MODE_PRIVATE).edit()
            .putString("last_error", value).putString("state", "error").apply()
    }

    override fun onDestroy() {
        desiredRecording = false
        destroying = true
        cameraHandler.post { stopSegment() }
        remoteForegroundReady = false
        if (instance === this) instance = null
        super.onDestroy()
    }

    companion object {
        const val ACTION_ARM = "org.meteormesh.node.ARM"
        const val ACTION_START = "org.meteormesh.node.START"
        const val ACTION_STOP = "org.meteormesh.node.STOP"
        const val EXTRA_PROFILE = "profile"
        private const val CHANNEL_ID = "meteor-capture"
        private const val NOTIFICATION_ID = 421
        @Volatile private var instance: MeteorCaptureService? = null
        /** A remote call cannot create a new camera FGS from Android background. */
        fun remoteStart(context: Context): Boolean {
            val s = instance ?: return false
            if (!s.remoteForegroundReady || !RemoteSettings.enabled(context)) return false
            RemoteSettings.noteCommand(context, "remote_start_queued")
            Handler(Looper.getMainLooper()).post {
                if (s.remoteForegroundReady && RemoteSettings.enabled(s)) {
                    s.onStartCommand(Intent(s, MeteorCaptureService::class.java).apply {
                        action = ACTION_START
                        putExtra(EXTRA_PROFILE, RemoteSettings.profile(s))
                    }, 0, 0)
                }
            }
            return true
        }
        fun remoteStop(context: Context): Boolean {
            val s = instance ?: return false
            if (!s.remoteForegroundReady || !RemoteSettings.enabled(context)) return false
            RemoteSettings.noteCommand(context, "remote_stop_queued")
            Handler(Looper.getMainLooper()).post {
                s.onStartCommand(Intent(s, MeteorCaptureService::class.java).apply {
                    action = ACTION_STOP
                }, 0, 0)
            }
            return true
        }
        fun readyForRemote(context: Context): Boolean =
            RemoteSettings.enabled(context) && instance?.remoteForegroundReady == true
        private const val MAX_RETRIES = 3
        private const val RETRY_DELAY_MS = 2_000L
        private const val ENCODER_STOP_TIMEOUT_MS = 10_000L
        private const val ENCODER_STOP_RETRY_MS = 250L
    }
}
