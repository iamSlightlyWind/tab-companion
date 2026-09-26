plugins {
	alias(libs.plugins.android.application)
	alias(libs.plugins.kotlin.android)
	alias(libs.plugins.kotlin.compose)
}

android {
	namespace = "com.tabcompanion.x810.bootswitcher"
	compileSdk = 35

	defaultConfig {
		applicationId = "com.tabcompanion.x810.bootswitcher"
		// The tablet runs Android 16; nothing here needs to reach further back
		// than the One UI 8 it ships with.
		minSdk = 34
		targetSdk = 35
		versionCode = 1
		versionName = "1.0.0-x810.1"
	}

	buildTypes {
		release {
			isMinifyEnabled = false
		}
		debug {
			// The app is installed by adb on one tablet, so a debug build is
			// the shipping build.  Keeping the same applicationId means an
			// install never leaves two copies behind.
			isMinifyEnabled = false
		}
	}

	compileOptions {
		sourceCompatibility = JavaVersion.VERSION_17
		targetCompatibility = JavaVersion.VERSION_17
	}

	kotlinOptions {
		jvmTarget = "17"
	}

	buildFeatures {
		compose = true
		buildConfig = true
	}
}

dependencies {
	testImplementation("junit:junit:4.13.2")
	implementation(libs.androidx.core.ktx)
	implementation(libs.androidx.lifecycle.runtime.ktx)
	implementation(libs.androidx.lifecycle.runtime.compose)
	implementation(libs.androidx.lifecycle.viewmodel.compose)
	implementation(libs.androidx.activity.compose)
	implementation(platform(libs.androidx.compose.bom))
	implementation(libs.androidx.ui)
	implementation(libs.androidx.ui.graphics)
	implementation(libs.androidx.ui.tooling.preview)
	implementation(libs.androidx.material3)
	implementation(libs.androidx.material.icons.extended)
}
