fn main() {
    // Every build from this repository includes the local sync integration.
    println!("cargo:rustc-env=CASE_LOCAL_SYNC_BUILD=1");
    tauri_build::build()
}
