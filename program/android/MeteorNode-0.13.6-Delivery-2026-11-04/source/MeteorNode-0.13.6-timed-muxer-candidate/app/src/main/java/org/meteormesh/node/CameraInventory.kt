package org.meteormesh.node

import android.content.Context
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CameraManager
import android.hardware.camera2.CameraMetadata
import android.media.MediaCodec
import android.os.Build
import android.util.Range
import android.util.Size
import org.json.JSONArray
import org.json.JSONObject

/** All values come from the local camera HAL. Never assume a phone supports manual exposure. */
object CameraInventory {
    fun backCameraId(context: Context): String {
        val manager = context.getSystemService(Context.CAMERA_SERVICE) as CameraManager
        return manager.cameraIdList.firstOrNull { id ->
            manager.getCameraCharacteristics(id).get(CameraCharacteristics.LENS_FACING) ==
                CameraCharacteristics.LENS_FACING_BACK
        } ?: error("Arka kamera bulunamadi")
    }

    fun cameraCharacteristics(context: Context, cameraId: String): CameraCharacteristics {
        return (context.getSystemService(Context.CAMERA_SERVICE) as CameraManager)
            .getCameraCharacteristics(cameraId)
    }

    fun supportsManual(c: CameraCharacteristics): Boolean {
        val caps = c.get(CameraCharacteristics.REQUEST_AVAILABLE_CAPABILITIES) ?: return false
        return caps.contains(CameraMetadata.REQUEST_AVAILABLE_CAPABILITIES_MANUAL_SENSOR)
    }

    fun videoSize(c: CameraCharacteristics): Size {
        val supported = c.get(CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP)
            ?.getOutputSizes(MediaCodec::class.java) ?: error("Video encoder boyutu yok")
        return supported.firstOrNull { it.width == 1920 && it.height == 1080 }
            ?: supported.firstOrNull { it.width == 1280 && it.height == 720 }
            ?: supported.filter { it.width <= 1920 && it.height <= 1080 }
                .maxByOrNull { it.width.toLong() * it.height }
            ?: supported.minByOrNull { it.width.toLong() * it.height }
            ?: error("Desteklenen video boyutu yok")
    }

    fun fps(c: CameraCharacteristics): Pair<Int, Range<Int>?> {
        val ranges = c.get(CameraCharacteristics.CONTROL_AE_AVAILABLE_TARGET_FPS_RANGES)
            .orEmpty()
        if (ranges.isEmpty()) error("FPS ranges unavailable in Camera2 capabilities")
        val best = ranges.minByOrNull { range ->
            val contains25 = if (range.lower <= 25 && range.upper >= 25) 0 else 200
            val uneven = range.upper - range.lower
            contains25 + kotlin.math.abs(range.upper - 25) + uneven
        } ?: ranges[0]
        // Constant video FPS cannot be assumed from variable-fps capture metadata.
        return best.upper to best
    }

    fun json(context: Context): JSONObject {
        val manager = context.getSystemService(Context.CAMERA_SERVICE) as CameraManager
        val cameras = JSONArray()
        manager.cameraIdList.forEach { id ->
            try {
                val c = manager.getCameraCharacteristics(id)
                val iso = c.get(CameraCharacteristics.SENSOR_INFO_SENSITIVITY_RANGE)
                val exposure = c.get(CameraCharacteristics.SENSOR_INFO_EXPOSURE_TIME_RANGE)
                val sensorClock = c.get(CameraCharacteristics.SENSOR_INFO_TIMESTAMP_SOURCE)
                val resolutions = c.get(CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP)
                    ?.getOutputSizes(MediaCodec::class.java).orEmpty()
                val fpsRanges = c.get(CameraCharacteristics.CONTROL_AE_AVAILABLE_TARGET_FPS_RANGES)
                    .orEmpty()
                cameras.put(JSONObject().apply {
                    put("id", id)
                    put("lens_facing", c.get(CameraCharacteristics.LENS_FACING))
                    put("hardware_level", c.get(CameraCharacteristics.INFO_SUPPORTED_HARDWARE_LEVEL))
                    put("manual_sensor", supportsManual(c))
                    put("iso_min", iso?.lower ?: JSONObject.NULL)
                    put("iso_max", iso?.upper ?: JSONObject.NULL)
                    put("exposure_ns_min", exposure?.lower ?: JSONObject.NULL)
                    put("exposure_ns_max", exposure?.upper ?: JSONObject.NULL)
                    put("sensor_timestamp_source", when (sensorClock) {
                        CameraMetadata.SENSOR_INFO_TIMESTAMP_SOURCE_REALTIME -> "REALTIME"
                        else -> "UNKNOWN"
                    })
                    put("video_sizes", JSONArray(resolutions.take(24).map { "${it.width}x${it.height}" }))
                    put("fps_ranges", JSONArray(fpsRanges.map { "${it.lower}-${it.upper}" }))
                })
            } catch (e: Exception) {
                cameras.put(JSONObject().put("id", id).put("error", e.message ?: "error"))
            }
        }
        return JSONObject().apply {
            put("device", Build.DEVICE)
            put("model", Build.MODEL)
            put("manufacturer", Build.MANUFACTURER)
            put("android_sdk", Build.VERSION.SDK_INT)
            put("cameras", cameras)
        }
    }
}
