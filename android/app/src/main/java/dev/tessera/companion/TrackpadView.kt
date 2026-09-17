package dev.tessera.companion

import android.content.Context
import android.util.AttributeSet
import android.view.MotionEvent
import android.view.View
import android.view.ViewConfiguration
import kotlin.math.abs
import kotlin.math.hypot

/**
 * A trackpad: one finger moves the pointer, a tap clicks, two fingers scroll,
 * a two-finger tap right-clicks, and a double tap that holds drags.
 */
class TrackpadView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
) : View(context, attrs) {

    interface Listener {
        fun onMove(dx: Float, dy: Float)
        fun onScroll(dx: Float, dy: Float)
        fun onClick(button: String)
        fun onButton(button: String, down: Boolean)
    }

    var listener: Listener? = null

    private val slop = ViewConfiguration.get(context).scaledTouchSlop.toFloat()
    private val tapTimeout = ViewConfiguration.getTapTimeout().toLong()
    private val doubleTapTimeout = ViewConfiguration.getDoubleTapTimeout().toLong()

    /** Pixels per millimetre of this screen, so travel is sent in millimetres
     *  and the computer scales it to its own screen. */
    private val pxPerMmX = resources.displayMetrics.xdpi / 25.4f
    private val pxPerMmY = resources.displayMetrics.ydpi / 25.4f

    private var lastX = 0f
    private var lastY = 0f
    private var downX = 0f
    private var downY = 0f
    private var downAt = 0L
    private var fingers = 0
    private var moved = false
    private var dragging = false
    private var lastTapAt = 0L
    private var pendingMove = 0f to 0f
    private var lastSent = 0L
    private var lastMoveAt = 0L

    /** One message per frame of this display, read as each touch begins: the
     *  rate changes with content on an adaptive panel. */
    private var frameMs = 8L

    init {
        isClickable = true
    }

    override fun onTouchEvent(event: MotionEvent): Boolean {
        when (event.actionMasked) {
            MotionEvent.ACTION_DOWN -> {
                fingers = 1
                moved = false
                downX = event.x
                downY = event.y
                lastX = event.x
                lastY = event.y
                downAt = event.eventTime
                lastMoveAt = event.eventTime
                val hz = display?.refreshRate?.takeIf { it > 1f } ?: 60f
                frameMs = (1000f / hz).toLong().coerceAtLeast(1L)
                // A second tap straight after the first, held down, drags.
                if (event.eventTime - lastTapAt < doubleTapTimeout) {
                    dragging = true
                    listener?.onButton("left", true)
                }
            }
            MotionEvent.ACTION_POINTER_DOWN -> {
                fingers = event.pointerCount
                lastX = event.getX(0)
                lastY = event.getY(0)
            }
            MotionEvent.ACTION_MOVE -> {
                val x = event.getX(0)
                val y = event.getY(0)
                val dx = (x - lastX) / pxPerMmX
                val dy = (y - lastY) / pxPerMmY
                lastX = x
                lastY = y
                if (!moved && hypot(x - downX, y - downY) > slop) moved = true
                if (!moved) return true
                if (fingers >= 2) {
                    listener?.onScroll(dx, dy)
                } else {
                    // Slow moves stay precise; a flick crosses the screen. Gain
                    // rises with finger speed, as a laptop trackpad's does.
                    val elapsed = (event.eventTime - lastMoveAt).coerceAtLeast(1L)
                    val speed = hypot(dx, dy) / elapsed
                    val gain = BASE_GAIN + ACCELERATION * minOf(speed, SPEED_CAP)
                    pendingMove = (pendingMove.first + dx * gain) to (pendingMove.second + dy * gain)
                    if (event.eventTime - lastSent >= frameMs) flush()
                }
                lastMoveAt = event.eventTime
            }
            MotionEvent.ACTION_POINTER_UP -> {
                // Two fingers lifted together without moving: a right click.
                if (!moved && fingers >= 2 && event.eventTime - downAt < tapTimeout * 3) {
                    listener?.onClick("right")
                    moved = true
                }
            }
            MotionEvent.ACTION_UP -> {
                flush()
                if (dragging) {
                    listener?.onButton("left", false)
                    dragging = false
                } else if (!moved && fingers == 1 && event.eventTime - downAt < tapTimeout * 3) {
                    listener?.onClick("left")
                    lastTapAt = event.eventTime
                }
                fingers = 0
            }
            MotionEvent.ACTION_CANCEL -> {
                if (dragging) listener?.onButton("left", false)
                dragging = false
                fingers = 0
            }
        }
        return true
    }

    private fun flush() {
        val (dx, dy) = pendingMove
        if (abs(dx) < 0.01f && abs(dy) < 0.01f) return
        pendingMove = 0f to 0f
        // The same clock as event.eventTime; wall time never compared.
        lastSent = android.os.SystemClock.uptimeMillis()
        listener?.onMove(dx, dy)
    }

    private companion object {
        const val BASE_GAIN = 1.0f
        /** Extra gain per millimetre-per-millisecond of finger speed. */
        const val ACCELERATION = 11f
        /** Finger speed, mm/ms, past which gain stops rising (about 2.8x). */
        const val SPEED_CAP = 0.16f
    }
}
