plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "org.meteormesh.node"
    compileSdk = 35
    buildToolsVersion = "35.0.0"
    buildFeatures { buildConfig = true }

    defaultConfig {
        applicationId = "org.meteormesh.node.compactfixedtest"
        minSdk = 26
        targetSdk = 35
        versionCode = 19
        versionName = "0.13.6-pin0000-timed-muxer-beta1"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
}
