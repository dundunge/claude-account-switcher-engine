//! Narrow macOS Accessibility adapter. Never reads input values or chat bodies.
use super::{Row, normalize, window};
use crate::{platform, usage};
use std::ffi::{CString, c_char, c_void};
use std::path::Path;
use std::ptr;
use std::time::{Duration, Instant};

type Ref = *const c_void;
#[link(name = "ApplicationServices", kind = "framework")]
unsafe extern "C" {
    fn AXIsProcessTrusted() -> bool;
    fn AXIsProcessTrustedWithOptions(options: Ref) -> bool;
    static kAXTrustedCheckOptionPrompt: Ref;
    fn AXUIElementCreateApplication(pid: i32) -> Ref;
    fn AXUIElementCopyAttributeValue(element: Ref, name: Ref, value: *mut Ref) -> i32;
    fn AXUIElementPerformAction(element: Ref, action: Ref) -> i32;
    fn AXUIElementSetMessagingTimeout(element: Ref, seconds: f32) -> i32;
    fn AXUIElementGetTypeID() -> usize;
}
#[link(name = "CoreFoundation", kind = "framework")]
unsafe extern "C" {
    fn CFRelease(value: Ref);
    fn CFRetain(value: Ref) -> Ref;
    fn CFGetTypeID(value: Ref) -> usize;
    fn CFStringGetTypeID() -> usize;
    fn CFArrayGetTypeID() -> usize;
    fn CFNumberGetTypeID() -> usize;
    fn CFStringCreateWithCString(allocator: Ref, text: *const c_char, encoding: u32) -> Ref;
    fn CFStringGetCString(value: Ref, buffer: *mut c_char, size: isize, encoding: u32) -> bool;
    fn CFNumberGetValue(value: Ref, kind: i32, out: *mut f64) -> bool;
    fn CFArrayGetCount(array: Ref) -> isize;
    fn CFArrayGetValueAtIndex(array: Ref, index: isize) -> Ref;
    fn CFDictionaryCreate(
        allocator: Ref,
        keys: *const Ref,
        values: *const Ref,
        count: isize,
        key_callbacks: Ref,
        value_callbacks: Ref,
    ) -> Ref;
    static kCFBooleanTrue: Ref;
}

struct Owned(Ref);
impl Drop for Owned {
    fn drop(&mut self) {
        if !self.0.is_null() {
            unsafe { CFRelease(self.0) };
        }
    }
}
impl Owned {
    fn string(text: &str) -> Self {
        let text = CString::new(text).expect("fixed AX attribute");
        Self(unsafe { CFStringCreateWithCString(ptr::null(), text.as_ptr(), 0x08000100) })
    }
    fn attr(&self, name: &str) -> Option<Self> {
        let mut out = ptr::null();
        let result =
            unsafe { AXUIElementCopyAttributeValue(self.0, Self::string(name).0, &mut out) };
        (result == 0 && !out.is_null()).then(|| Self(out))
    }
    fn text(&self) -> Option<String> {
        if unsafe { CFGetTypeID(self.0) != CFStringGetTypeID() } {
            return None;
        }
        let mut out = [0u8; 1024];
        if !unsafe {
            CFStringGetCString(
                self.0,
                out.as_mut_ptr().cast(),
                out.len() as isize,
                0x08000100,
            )
        } {
            return None;
        }
        let end = out.iter().position(|&b| b == 0)?;
        String::from_utf8(out[..end].to_vec()).ok()
    }
    fn text_attr(&self, name: &str) -> String {
        self.attr(name).and_then(|v| v.text()).unwrap_or_default()
    }
    fn number(&self) -> Option<f64> {
        if unsafe { CFGetTypeID(self.0) == CFNumberGetTypeID() } {
            let mut result = 0.0;
            unsafe { CFNumberGetValue(self.0, 6, &mut result) }.then_some(result)
        } else {
            self.text()?.parse().ok()
        }
    }
    fn children(&self) -> Vec<Self> {
        let Some(array) = self.attr("AXChildren") else {
            return vec![];
        };
        if unsafe { CFGetTypeID(array.0) != CFArrayGetTypeID() } {
            return vec![];
        }
        (0..unsafe { CFArrayGetCount(array.0) }.min(4096))
            .filter_map(|i| {
                let value = unsafe { CFArrayGetValueAtIndex(array.0, i) };
                (!value.is_null() && unsafe { CFGetTypeID(value) == AXUIElementGetTypeID() })
                    .then(|| Self(unsafe { CFRetain(value) }))
            })
            .collect()
    }
    fn press(&self) -> bool {
        unsafe { AXUIElementPerformAction(self.0, Self::string("AXPress").0) == 0 }
    }
    fn retained(&self) -> Self {
        Self(unsafe { CFRetain(self.0) })
    }
}

struct Scan {
    anchor: Option<Owned>,
    rows: Vec<Row>,
    reset: Option<String>,
    heading: String,
}

fn usage_label(label: &str) -> bool {
    let text = label.trim().to_lowercase();
    text.starts_with("usage:")
        || text.starts_with("usage ")
        || text.starts_with("用量:")
        || text.starts_with("用量：")
        || text.starts_with("用量 ")
        || text.starts_with("额度:")
        || text.starts_with("额度：")
        || text.starts_with("额度 ")
}

fn panel_role(role: &str) -> bool {
    matches!(role, "AXPopover" | "AXSheet" | "AXDialog")
}

/// Find the Usage control and the small AX subtrees that can contain its
/// popover.  The application root is used only for discovery; quota values are
/// read in `scan_panel`, never from arbitrary siblings such as chat messages.
fn scan(app: &Owned) -> Scan {
    let mut result = Scan {
        anchor: None,
        rows: vec![],
        reset: None,
        heading: String::new(),
    };
    let mut panels = Vec::new();
    let mut stack = vec![(app.retained(), 0)];
    let deadline = Instant::now() + Duration::from_secs(4);
    let mut seen = 0;
    while let Some((element, depth)) = stack.pop() {
        seen += 1;
        if seen > 4096 || Instant::now() > deadline {
            break;
        }
        let role = element.text_attr("AXRole");
        // A chat is a sibling of the quota controls, never their parent.
        // No AXValue is fetched for text inputs, chat messages or session text.
        let description = element.text_attr("AXDescription");
        if role == "AXTextArea" || role == "AXTextField" {
            continue;
        }
        let title = element.text_attr("AXTitle");
        let label = if description.is_empty() {
            title
        } else {
            description
        };
        if matches!(role.as_str(), "AXPopUpButton" | "AXButton" | "AXMenuButton")
            && usage_label(&label)
        {
            result.anchor = Some(element.retained());
        }
        if panel_role(&role) || (role == "AXGroup" && usage_label(&label)) {
            panels.push(element.retained());
        }
        if label == "Chat messages"
            || label.starts_with("Message ")
            || label == "Currently streaming message"
        {
            continue;
        }
        if depth < 40 {
            stack.extend(element.children().into_iter().rev().map(|v| (v, depth + 1)));
        }
    }

    // A popover can expose its rows below the Usage button itself on older
    // Electron builds, while newer builds expose an AXPopover sibling.  Scan
    // both narrowly scoped roots; neither path re-enters the whole application.
    if let Some(anchor) = &result.anchor {
        panels.push(anchor.retained());
    }
    for panel in panels {
        scan_panel(&panel, &mut result);
    }
    result
}

fn scan_panel(root: &Owned, result: &mut Scan) {
    let mut stack = vec![(root.retained(), 0)];
    let deadline = Instant::now() + Duration::from_secs(2);
    let mut seen = 0;
    while let Some((element, depth)) = stack.pop() {
        seen += 1;
        if seen > 2048 || Instant::now() > deadline {
            break;
        }
        let role = element.text_attr("AXRole");
        // A chat is a sibling of the quota controls, never their parent.
        // No AXValue is fetched for text inputs, chat messages or session text.
        let description = element.text_attr("AXDescription");
        if role == "AXTextArea" || role == "AXTextField" {
            continue;
        }
        let title = element.text_attr("AXTitle");
        let label = if description.is_empty() {
            title
        } else {
            description
        };
        if role == "AXStaticText" {
            // Only read static text in reset/limit labels; never persist it as
            // arbitrary app text. Electron sometimes exposes the label in Value.
            let text = if label.is_empty() {
                element.text_attr("AXValue")
            } else {
                label.clone()
            };
            if text.starts_with("Resets ") || text.starts_with("重置") || text.contains("后重置")
            {
                result.reset = Some(text);
            } else if window(&text).is_some() || text.starts_with("Weekly") || text.contains("模型")
            {
                result.heading = text;
                result.reset = None;
            }
        }
        if role == "AXLevelIndicator" || role == "AXProgressIndicator" {
            let label = if label.is_empty() {
                result.heading.clone()
            } else {
                label
            };
            if window(&label).is_some()
                && let Some(percent) = element.attr("AXValue").and_then(|v| v.number())
            {
                result.rows.push(Row {
                    label,
                    percent,
                    reset: result.reset.take(),
                });
            }
        }
        if depth < 40 {
            stack.extend(element.children().into_iter().rev().map(|v| (v, depth + 1)));
        }
    }
}

struct ClosePopover(Option<Owned>);
impl Drop for ClosePopover {
    fn drop(&mut self) {
        if let Some(anchor) = &self.0 {
            let _ = anchor.press();
        }
    }
}

pub(super) fn capture(profile: &Path) -> Result<usage::Usage, &'static str> {
    let pids = platform::pids_for_profile(profile);
    if pids.is_empty() {
        return Err("ACCOUNT_NOT_OPEN");
    }
    if pids.len() != 1 {
        return Err("PROFILE_PROCESS_AMBIGUOUS");
    }
    if !unsafe { AXIsProcessTrusted() } {
        // Called only by the user's Read action, never by status polling.
        unsafe {
            let options = Owned(CFDictionaryCreate(
                ptr::null(),
                &kAXTrustedCheckOptionPrompt,
                &kCFBooleanTrue,
                1,
                ptr::null(),
                ptr::null(),
            ));
            if !options.0.is_null() {
                AXIsProcessTrustedWithOptions(options.0);
            }
        }
        return Err("ACCESSIBILITY_REQUIRED");
    }
    let pid: i32 = pids[0].parse().map_err(|_| "ACCOUNT_NOT_OPEN")?;
    let app = Owned(unsafe { AXUIElementCreateApplication(pid) });
    if app.0.is_null() {
        return Err("PANEL_UNAVAILABLE");
    }
    unsafe { AXUIElementSetMessagingTimeout(app.0, 0.25) };
    let first = scan(&app);
    if let Ok(value) = normalize(first.rows, crate::now_ms()) {
        return Ok(value);
    }
    let anchor = first.anchor.ok_or("PANEL_UNAVAILABLE")?;
    if !anchor.press() {
        return Err("PANEL_UNAVAILABLE");
    }
    let _close = ClosePopover(Some(anchor));
    for _ in 0..3 {
        std::thread::sleep(Duration::from_millis(180));
        if let Ok(value) = normalize(scan(&app).rows, crate::now_ms()) {
            return Ok(value);
        }
    }
    Err("PANEL_UNRECOGNIZED")
}
