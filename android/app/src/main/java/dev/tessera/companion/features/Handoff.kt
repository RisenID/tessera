package dev.tessera.companion.features

import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.net.Uri

/** Links sent from the computer, opened on this phone. */
object Handoff {

    private val SCHEMES = setOf("http", "https", "mailto", "tel", "geo", "sms")

    /** Opens [url] with whatever the phone uses for it. Null when it worked. */
    fun open(context: Context, url: String): String? {
        val uri = runCatching { Uri.parse(url.trim()) }.getOrNull()
            ?: return "That is not a link."
        if (uri.scheme?.lowercase() !in SCHEMES) return "Only web, mail, phone and map links can be opened."
        val intent = Intent(Intent.ACTION_VIEW, uri).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        return try {
            context.startActivity(intent)
            null
        } catch (_: ActivityNotFoundException) {
            "No app on the phone opens that kind of link."
        } catch (e: Exception) {
            "The phone could not open it: ${e.message}"
        }
    }
}
