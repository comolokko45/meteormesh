package org.meteormesh.node

/** Storage and segment-size policy shared by recording, UI diagnostics and the health API. */
object RecordingPolicy {
    const val PROTECTED_RESERVE_BYTES = 32L * 1024L * 1024L * 1024L
    const val FILE_HEADROOM_BYTES = 128L * 1024L * 1024L
    private const val MIN_FILE_LIMIT_BYTES = 8L * 1024L * 1024L
    private const val EMERGENCY_MARGIN_DIVISOR = 4L

    /** Nominal bytes expected at the requested bitrate and PTS duration. */
    fun expectedSegmentBytes(videoBitrateBitsPerSecond: Int, segmentDurationMs: Long): Long {
        val estimate = videoBitrateBitsPerSecond.toLong() * segmentDurationMs / 8_000L
        return estimate.coerceAtLeast(MIN_FILE_LIMIT_BYTES)
    }

    /**
     * Emergency-only ceiling. Normal rollover is driven by encoder PTS, not file size.
     * A 25% margin prevents the nominal 300 s stream from being cut near 270 s while
     * still bounding an encoder that materially overshoots its configured bitrate.
     */
    fun emergencyFileLimitBytes(expectedSegmentBytes: Long): Long {
        val margin = (expectedSegmentBytes + EMERGENCY_MARGIN_DIVISOR - 1L) / EMERGENCY_MARGIN_DIVISOR
        return Math.addExact(expectedSegmentBytes, margin)
    }

    fun requiredFreeBytes(nextEmergencyFileLimitBytes: Long): Long =
        PROTECTED_RESERVE_BYTES + nextEmergencyFileLimitBytes + FILE_HEADROOM_BYTES
}
