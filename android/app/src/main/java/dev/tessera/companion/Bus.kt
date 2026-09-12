package dev.tessera.companion

import org.json.JSONObject
import java.util.concurrent.CopyOnWriteArrayList

/**
 * Fan-out from the phone's event sources to whichever desktops are connected.
 *
 * Keeping this separate means NotificationBridge does not need to know whether
 * anyone is listening: it publishes, and sessions subscribe while they live.
 */
object Bus {

    fun interface Subscriber {
        fun onEvent(message: JSONObject)
    }

    private val subscribers = CopyOnWriteArrayList<Subscriber>()

    fun subscribe(subscriber: Subscriber) {
        subscribers.add(subscriber)
    }

    fun unsubscribe(subscriber: Subscriber) {
        subscribers.remove(subscriber)
    }

    fun publish(message: JSONObject) {
        // CopyOnWriteArrayList lets a subscriber unsubscribe from inside its own
        // callback without a ConcurrentModificationException.
        for (subscriber in subscribers) {
            runCatching { subscriber.onEvent(message) }
        }
    }

    val hasSubscribers: Boolean
        get() = subscribers.isNotEmpty()
}
