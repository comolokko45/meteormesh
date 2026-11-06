plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "org.meteormesh.mobile"
    compileSdk = 35
    buildToolsVersion = "35.0.0"

    defaultConfig {
        applicationId = "org.meteormesh.mobile"
        minSdk = 26
        targetSdk = 35
        versionCode = 4
        versionName = "1.2.0"
        buildConfigField("String", "MONITOR_HOST", "\"meteorhub.tail520fb9.ts.net\"")
    }

    signingConfigs {
        create("release") {
            val path = System.getenv("METEORMESH_SIGNING_STORE")
            val password = System.getenv("METEORMESH_SIGNING_PASSWORD")
            if (!path.isNullOrBlank() && !password.isNullOrBlank()) {
                storeFile = file(path)
                storePassword = password
                keyAlias = "meteormesh-mobile"
                keyPassword = password
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = signingConfigs.getByName("release")
        }
    }

    buildFeatures { buildConfig = true }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
}

dependencies {
    implementation("androidx.core:core-ktx:1.15.0")
    implementation("androidx.swiperefreshlayout:swiperefreshlayout:1.1.0")
}
