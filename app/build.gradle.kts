plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jlleitschuh.gradle.ktlint")
    id("io.gitlab.arturbosch.detekt")
}

ktlint {
    android.set(true)
}

detekt {
    config.setFrom("$rootDir/config/detekt.yml")
    buildUponDefaultConfig = false
}

android {
    namespace = "com.gsm2computer.bridge"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.gsm2computer.bridge"
        minSdk = 26
        targetSdk = 34
        // versionCode 345 walks the ordered hub list on top of build 344.
        versionCode = 345
        versionName = "0.2.6"

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"

        buildConfigField("boolean", "DEFAULT_STREAM_ENABLED", "true")
        buildConfigField("String", "DEFAULT_HUB_CONTROL_URL", "\"http://hub.mining-ling.ts.net:8787\"")
        buildConfigField(
            "String",
            "DEFAULT_HUB_ENDPOINTS",
            "\"wss://gsm.ta3leam.com/\\nws://100.113.222.47:8787/\"",
        )
        buildConfigField("String", "DEFAULT_DEVICE_TOKEN", "\"\"")
        buildConfigField("String", "DEFAULT_STREAM_TOKEN_URL", "\"\"")
        buildConfigField("String", "DEFAULT_STREAM_MODEL", "\"gpt-realtime\"")
        buildConfigField("String", "DEFAULT_STREAM_VOICE", "\"marin\"")
    }

    signingConfigs {
        create("release") {
            storeFile = file(System.getProperty("user.home") + "/.android/debug.keystore")
            storePassword = "android"
            keyAlias = "androiddebugkey"
            keyPassword = "android"
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            signingConfig = signingConfigs.getByName("release")
        }
    }

    buildFeatures {
        buildConfig = true
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_1_8
        targetCompatibility = JavaVersion.VERSION_1_8
    }

    kotlinOptions {
        jvmTarget = "1.8"
    }

    lint {
        baseline = file("lint-baseline.xml")
    }
}

tasks.withType<org.gradle.api.tasks.testing.Test>().configureEach {
    val releaseFlag = System.getenv("GSM2COMPUTER_BRIDGE_PATH_RELEASE")
    if (!releaseFlag.isNullOrEmpty()) {
        environment("GSM2COMPUTER_BRIDGE_PATH_RELEASE", releaseFlag)
    }
    val verifyState = System.getenv("GSM2COMPUTER_VERIFY_STATE")
    if (!verifyState.isNullOrEmpty()) {
        environment("GSM2COMPUTER_VERIFY_STATE", verifyState)
    }
    val verifyEvidence = System.getenv("GSM2COMPUTER_VERIFY_EVIDENCE")
    if (!verifyEvidence.isNullOrEmpty()) {
        environment("GSM2COMPUTER_VERIFY_EVIDENCE", verifyEvidence)
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.12.0")
    implementation("androidx.appcompat:appcompat:1.6.1")
    implementation("com.google.android.material:material:1.11.0")
    implementation("androidx.constraintlayout:constraintlayout:2.1.4")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    testImplementation("junit:junit:4.13.2")
    testImplementation("com.squareup.okhttp3:mockwebserver:4.12.0")
    androidTestImplementation("androidx.test.ext:junit:1.1.5")
    androidTestImplementation("androidx.test:runner:1.5.2")
}
