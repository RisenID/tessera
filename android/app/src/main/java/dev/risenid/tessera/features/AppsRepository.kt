package dev.risenid.tessera.features

import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import org.json.JSONArray
import org.json.JSONObject

/** The phone's launchable apps. */
object AppsRepository {

    fun launchable(context: Context): JSONArray {
        val manager = context.packageManager
        val intent = Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER)

        val resolved = runCatching {
            // ResolveInfoFlags arrived in API 33; below that only the int
            // overload exists, and calling the new one would crash.
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                manager.queryIntentActivities(intent, PackageManager.ResolveInfoFlags.of(0L))
            } else {
                @Suppress("DEPRECATION")
                manager.queryIntentActivities(intent, 0)
            }
        }.getOrElse { emptyList() }

        val seen = HashSet<String>()
        val apps = resolved.mapNotNull { info ->
            val packageName = info.activityInfo?.packageName ?: return@mapNotNull null
            // A package can expose several launcher activities; one entry each.
            if (!seen.add(packageName)) return@mapNotNull null
            JSONObject()
                .put("package", packageName)
                .put("name", runCatching { info.loadLabel(manager).toString() }.getOrDefault(packageName))
                .put("system", isSystem(manager, packageName))
        }.sortedBy { it.optString("name").lowercase() }

        return JSONArray(apps)
    }

    private fun isSystem(manager: PackageManager, packageName: String): Boolean = runCatching {
        val flags = manager.getApplicationInfo(packageName, 0).flags
        flags and android.content.pm.ApplicationInfo.FLAG_SYSTEM != 0
    }.getOrDefault(false)

    /** Brings an app to the foreground on the phone's own screen. */
    fun launch(context: Context, packageName: String): Boolean {
        val intent = context.packageManager.getLaunchIntentForPackage(packageName)
            ?: return false
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        return runCatching { context.startActivity(intent); true }.getOrDefault(false)
    }
}
