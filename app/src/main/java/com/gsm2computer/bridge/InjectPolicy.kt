package com.gsm2computer.bridge

/**
 * Moto g84 injection experiment, chosen at call start from
 * [PROPERTY]. The profile is cached for the process, so the property is
 * read again on each call and a reinstall is not required to switch.
 *
 * Every policy lets the HAL own Incall_Music. They differ only in the
 * Voice Tx Mute tuple and AudioManager.isMicrophoneMute.
 */
enum class InjectPolicy(
    val wire: String,
    val writesVoiceTxMute: Boolean,
    val micApi: MicApiAction,
) {
    HAL_OWNED("hal_owned", true, MicApiAction.FORCE_FALSE),
    HAL_OWNED_NO_UNMUTE("hal_owned_no_unmute", false, MicApiAction.LEAVE),
    HAL_OWNED_API_MUTE("hal_owned_api_mute", false, MicApiAction.FORCE_TRUE),
    ;

    companion object {
        const val PROPERTY = "persist.gsm2computer.inject_policy"
        val DEFAULT = HAL_OWNED_API_MUTE

        fun parse(raw: String?): InjectPolicy {
            val key = raw?.trim()?.lowercase().orEmpty()
            return entries.firstOrNull { it.wire == key } ?: DEFAULT
        }

        fun recognized(raw: String?): Boolean {
            val key = raw?.trim().orEmpty()
            if (key.isEmpty()) return true
            return entries.any { it.wire == key.lowercase() }
        }

        /** Blank when the property is unset or Android is not on the classpath. */
        fun rawProperty(get: (String) -> String? = ::systemProperty): String =
            get(PROPERTY).orEmpty()

        private fun systemProperty(key: String): String? = try {
            val cls = Class.forName("android.os.SystemProperties")
            val get = cls.getMethod("get", String::class.java, String::class.java)
            get.invoke(null, key, "") as? String
        } catch (_: Throwable) {
            ""
        }
    }
}

/** What the Moto bridge does to AudioManager.isMicrophoneMute at call start. */
enum class MicApiAction(val wire: String) {
    FORCE_FALSE("set-false"),
    LEAVE("leave"),
    FORCE_TRUE("set-true"),
}
