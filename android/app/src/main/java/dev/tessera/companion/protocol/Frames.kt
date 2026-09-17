package dev.tessera.companion.protocol

import org.json.JSONObject
import java.io.DataInputStream
import java.io.EOFException
import java.io.InputStream
import java.io.OutputStream

/**
 * Wire framing for the desktop link: a 4-byte big-endian length, a type byte,
 * then the payload. See docs/PROTOCOL.md in the desktop repository.
 */
object Frames {
    const val TYPE_JSON: Int = 1
    const val TYPE_BINARY: Int = 2

    /** Refuse absurd frames instead of allocating whatever the peer claims. */
    const val MAX_FRAME: Int = 32 * 1024 * 1024

    data class Frame(val type: Int, val payload: ByteArray)

    fun read(input: DataInputStream): Frame {
        val length = try {
            input.readInt()
        } catch (e: EOFException) {
            throw ClosedException()
        }
        if (length < 1) throw ProtocolException("frame with no type byte")
        if (length > MAX_FRAME) throw ProtocolException("frame of $length bytes exceeds the limit")

        val type = input.readUnsignedByte()
        val payload = ByteArray(length - 1)
        input.readFully(payload)
        return Frame(type, payload)
    }

    /**
     * Writes one frame. Callers must hold the socket's write lock: a JSON
     * header and its binary payload have to stay adjacent on the wire.
     */
    fun write(output: OutputStream, type: Int, payload: ByteArray) {
        val length = payload.size + 1
        // The desktop drops the link on a larger frame.
        if (length > MAX_FRAME) throw ProtocolException("frame of $length bytes exceeds the limit")
        output.write((length ushr 24) and 0xFF)
        output.write((length ushr 16) and 0xFF)
        output.write((length ushr 8) and 0xFF)
        output.write(length and 0xFF)
        output.write(type)
        output.write(payload)
    }

    fun writeJson(output: OutputStream, message: JSONObject) {
        write(output, TYPE_JSON, message.toString().toByteArray(Charsets.UTF_8))
    }

    fun writeBinary(output: OutputStream, payload: ByteArray) {
        write(output, TYPE_BINARY, payload)
    }

    /** Skips the rest of a stream we no longer care about. */
    fun drain(input: InputStream) {
        try {
            while (input.read() >= 0) Unit
        } catch (_: Exception) {
        }
    }
}

class ProtocolException(message: String) : Exception(message)

/** The peer closed the connection cleanly; not an error worth logging loudly. */
class ClosedException : Exception("connection closed")
