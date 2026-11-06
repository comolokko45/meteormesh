package org.meteormesh.node

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import android.os.PowerManager
import android.provider.Settings
import org.json.JSONObject

/** Read-only view of relevant permission states; NEVER grants permissions by itself. */
object PermissionStatus {
    fun snapshot(context: Context): JSONObject {
        val power = context.getSystemService(Context.POWER_SERVICE) as PowerManager
        return JSONObject().apply {
            put("camera", context.checkSelfPermission(Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED)
            put("notifications", Build.VERSION.SDK_INT < 33 ||
                context.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED)
            put("write_system_settings_optional", Settings.System.canWrite(context))
            put("battery_optimization_exempt", power.isIgnoringBatteryOptimizations(context.packageName))
            put("all_android_permissions_automatic", false)
            put("camera_fgs_must_be_armed_from_visible_phone", true)
            put("android15_data_sync_lan_time_limit_hours", if (Build.VERSION.SDK_INT >= 35) 6 else JSONObject.NULL)
        }
    }
}
