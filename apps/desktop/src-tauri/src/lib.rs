mod backend;

use tauri::RunEvent;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .plugin(
            tauri_plugin_log::Builder::new()
                .level(log::LevelFilter::Info)
                .build(),
        )
        .manage(backend::BackendState::new())
        .invoke_handler(tauri::generate_handler![backend::backend_info, backend::restart_backend])
        .setup(|app| {
            backend::start(app.handle().clone());
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building LAZOS");

    app.run(|handle, event| {
        if let RunEvent::Exit = event {
            backend::stop(handle);
        }
    });
}
