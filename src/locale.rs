//! Shared interface language, including native menus. User data is never translated.
use std::sync::atomic::{AtomicBool, Ordering};
use tauri::menu::{Menu, MenuItemKind};
use tauri::{AppHandle, Wry};

static ENGLISH: AtomicBool = AtomicBool::new(false);

pub fn english() -> bool {
    ENGLISH.load(Ordering::Relaxed)
}
pub fn set(language: &str) {
    ENGLISH.store(language == "en", Ordering::Relaxed);
}
pub fn tr<'a>(zh: &'a str, en: &'a str) -> &'a str {
    if english() { en } else { zh }
}

#[macro_export]
macro_rules! l10n {
    ($zh:literal, $en:literal $(, $arg:expr)* $(,)?) => {
        if $crate::locale::english() { format!($en $(, $arg)*) }
        else { format!($zh $(, $arg)*) }
    };
}

fn label(text: &str) -> Option<&'static str> {
    Some(match text {
        "File" => "文件",
        "Edit" => "编辑",
        "View" => "显示",
        "Window" => "窗口",
        "Help" => "帮助",
        "Services" => "服务",
        "Undo" => "撤销",
        "Redo" => "重做",
        "Cut" => "剪切",
        "Copy" => "复制",
        "Paste" => "粘贴",
        "Select All" => "全选",
        "Minimize" => "最小化",
        "Zoom" | "Maximize" => "缩放",
        "Close" | "Close Window" => "关闭窗口",
        "Hide Others" => "隐藏其他应用",
        "Show All" => "全部显示",
        "Enter Full Screen" | "Toggle Full Screen" => "切换全屏",
        "Exit Full Screen" => "退出全屏",
        "About case" | "About CASE" => "关于 CASE",
        "Hide case" | "Hide CASE" => "隐藏 CASE",
        "Quit case" | "Quit CASE" => "退出 CASE",
        _ => return None,
    })
}

fn translate(items: Vec<MenuItemKind<Wry>>) -> tauri::Result<()> {
    for item in items {
        match item {
            MenuItemKind::Submenu(menu) => {
                if let Some(text) = label(&menu.text()?) {
                    menu.set_text(text)?;
                }
                translate(menu.items()?)?;
            }
            MenuItemKind::Predefined(item) => {
                if let Some(text) = label(&item.text()?) {
                    item.set_text(text)?;
                }
            }
            MenuItemKind::MenuItem(item) => {
                if let Some(text) = label(&item.text()?) {
                    item.set_text(text)?;
                }
            }
            _ => {}
        }
    }
    Ok(())
}

pub fn install(app: &AppHandle) -> tauri::Result<()> {
    let menu = Menu::default(app)?;
    if !english() {
        translate(menu.items()?)?;
    }
    app.set_menu(menu)?;
    Ok(())
}
