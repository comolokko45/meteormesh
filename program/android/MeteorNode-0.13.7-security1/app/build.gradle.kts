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
        versionCode = 20
        versionName = "0.13.7-pin0000-security1"
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

dependencies { testImplementation("junit:junit:4.13.2"); testImplementation("org.json:json:20240303") }
