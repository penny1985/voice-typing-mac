#!/usr/bin/env python3
"""
選單列版語音輸入 App（toggle 模式）。
按一下右 ⌘ 開始錄、再按一下停止並轉錄貼上，中間不用一直按著。
選單列圖示顯示狀態，並可編輯慣用語、查看最近紀錄。
"""

import sys
import subprocess

import objc
import rumps
from AppKit import (
    NSApplication, NSApplicationActivationPolicyAccessory,
    NSEvent, NSEventMaskFlagsChanged, NSView, NSPanel, NSColor, NSBezierPath,
    NSScreen, NSBackingStoreBuffered, NSStatusWindowLevel,
    NSWindowStyleMaskBorderless, NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorStationary,
)
from Foundation import NSMakeRect

from engine import (
    Engine, HISTORY_KEEP, PHRASES_FILE, REPLACE_FILE,
    load_phrases, load_replacements, add_phrase, add_replacement,
)

WAVE_BARS = 9          # 波形長條數
WAVE_GAIN = 14         # 音量放大倍率（讓長條跳得明顯）


class WaveView(NSView):
    """錄音波形：一排長條，高度跟著即時音量捲動跳動。"""

    def initWithFrame_(self, frame):
        self = objc.super(WaveView, self).initWithFrame_(frame)
        if self is None:
            return None
        self._levels = [0.0] * WAVE_BARS
        return self

    def setLevels_(self, levels):
        self._levels = levels
        self.setNeedsDisplay_(True)

    def drawRect_(self, rect):
        b = self.bounds()
        W, H = b.size.width, b.size.height
        # 白色圓角底 + 淺灰邊框（白底桌面也看得到輪廓）
        bg = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(b, 14, 14)
        NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.97).set()
        bg.fill()
        NSColor.colorWithCalibratedWhite_alpha_(0.0, 0.12).set()
        bg.setLineWidth_(1.0)
        bg.stroke()
        # 黑色長條
        bw, gap = 5.0, 5.0
        total = WAVE_BARS * bw + (WAVE_BARS - 1) * gap
        x = (W - total) / 2.0
        cy = H / 2.0
        maxh = H - 20
        NSColor.colorWithCalibratedWhite_alpha_(0.0, 1.0).set()
        for v in self._levels:
            bh = max(4.0, min(maxh, v * maxh))
            bar = NSMakeRect(x, cy - bh / 2.0, bw, bh)
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(bar, 2.5, 2.5).fill()
            x += bw + gap

# 讓 print 立刻寫進 log（不然會卡在緩衝區看不到）
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

# macOS 虛擬鍵碼：右 ⌘ = 54、右 ⌥ = 61；修飾鍵旗標 Command = 1<<20、Option = 1<<19
RIGHT_CMD_KEYCODE = 54
RIGHT_OPT_KEYCODE = 61
CMD_FLAG = 1 << 20
OPT_FLAG = 1 << 19

STATUS = {
    "idle":         ("🎤", "待機中（按一下右 ⌘ 開始）"),
    "recording":    ("🔴", "錄音中…再按一下右 ⌘ 停止"),
    "transcribing": ("⏳", "轉錄中…請稍候"),
}


def request_accessibility():
    """啟動時主動觸發輔助使用授權對話框，並把本程式加進系統清單。"""
    try:
        from ApplicationServices import AXIsProcessTrustedWithOptions
        trusted = AXIsProcessTrustedWithOptions({"AXTrustedCheckOptionPrompt": True})
        print(f"[debug] 輔助使用已授權：{trusted}")
    except Exception as e:
        print(f"[debug] 無法檢查輔助使用權限：{e}")


class VoiceApp(rumps.App):
    def __init__(self):
        super().__init__("🎤", quit_button=None)
        # 設成選單列背景模式：不在 Dock 顯示圖示，只留右上角 🎤
        NSApplication.sharedApplication().setActivationPolicy_(
            NSApplicationActivationPolicyAccessory
        )
        request_accessibility()
        load_phrases()        # 確保 慣用語.txt 存在
        load_replacements()   # 確保 修正規則.txt 存在

        self.status_item = rumps.MenuItem(STATUS["idle"][1])
        self.hist_items = [
            rumps.MenuItem("（尚無紀錄）", callback=self.copy_history),
            rumps.MenuItem("", callback=self.copy_history),
        ]
        self.hist_texts = ["", ""]
        self.menu = [
            self.status_item,
            None,
            rumps.MenuItem("➕ 快速加詞／修正…（快捷鍵：右 ⌥）", callback=self.quick_add),
            rumps.MenuItem("✏️ 編輯慣用語（辨識用詞）", callback=self.edit_phrases),
            rumps.MenuItem("🔧 編輯修正規則（錯＝對）", callback=self.edit_replace),
            None,
            rumps.MenuItem("🕘 最近紀錄（只存記憶體，關閉即清除）", callback=None),
            self.hist_items[0],
            self.hist_items[1],
            None,
            rumps.MenuItem("結束", callback=self.quit),
        ]
        self.history = []        # 只存記憶體，不寫硬碟
        self._refresh_history()

        self._pending = ("idle", None)
        self._last_state = "idle"
        self.engine = Engine(on_status=self._record_status)

        # 用 macOS 原生 NSEvent 監看（在主執行緒，避開 pynput 的跨執行緒崩潰）
        # 全域：其他 App 在前景時也收得到（需輔助使用權限）；本地：本 App 在前景時
        self._gmon = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
            NSEventMaskFlagsChanged, self._on_flags
        )
        self._lmon = NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
            NSEventMaskFlagsChanged, self._on_flags_local
        )
        rumps.Timer(self._refresh, 0.2).start()

        # 錄音波形浮動指示器
        self._wave_levels = [0.0] * WAVE_BARS
        self._build_wave_panel()
        rumps.Timer(self._tick_wave, 0.05).start()

    def _build_wave_panel(self):
        w, h = 150.0, 52.0
        scr = NSScreen.mainScreen().frame()
        rect = NSMakeRect(scr.size.width / 2.0 - w / 2.0, 130.0, w, h)
        self.panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            rect, NSWindowStyleMaskBorderless, NSBackingStoreBuffered, False
        )
        self.panel.setLevel_(NSStatusWindowLevel)
        self.panel.setOpaque_(False)
        self.panel.setBackgroundColor_(NSColor.clearColor())
        self.panel.setIgnoresMouseEvents_(True)
        self.panel.setHasShadow_(True)
        self.panel.setHidesOnDeactivate_(False)
        self.panel.setBecomesKeyOnlyIfNeeded_(True)
        self.panel.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorStationary
        )
        self.wave_view = WaveView.alloc().initWithFrame_(NSMakeRect(0, 0, w, h))
        self.panel.setContentView_(self.wave_view)

    def _tick_wave(self, _):
        if self.engine._recording:
            lv = min(1.0, self.engine.level * WAVE_GAIN)
            self._wave_levels = self._wave_levels[1:] + [lv]
            self.wave_view.setLevels_(self._wave_levels)
            if not self.panel.isVisible():
                self.panel.orderFrontRegardless()
        elif self.panel.isVisible():
            self.panel.orderOut_(None)

    # ---- 狀態顯示 ----
    def _record_status(self, state, text=None):
        self._pending = (state, text)

    def _refresh(self, _):
        state, text = self._pending
        if state == "done":
            self.title = "🎤"
            shown = text if len(text) <= 18 else text[:18] + "…"
            self.status_item.title = f"✓ 已貼上：{shown}"
        elif state == "error":
            self.title = "⚠️"
            self.status_item.title = f"錯誤：{text}"
        else:
            icon, label = STATUS.get(state, ("🎤", state))
            self.title = icon
            self.status_item.title = label
        # 轉錄完成時更新記憶體歷史
        if state == "done" and self._last_state != "done":
            if text:
                self.history.insert(0, text)
                self.history = self.history[:HISTORY_KEEP]
            self._refresh_history()
        self._last_state = state

    def _refresh_history(self):
        for i in range(2):
            if i < len(self.history):
                self.hist_texts[i] = self.history[i]
                t = self.history[i].replace("\n", " ")
                self.hist_items[i].title = t if len(t) <= 30 else t[:30] + "…"
            else:
                self.hist_texts[i] = ""
                self.hist_items[i].title = "（尚無紀錄）" if i == 0 else ""

    # ---- 選單動作 ----
    def copy_history(self, sender):
        for i, item in enumerate(self.hist_items):
            if item is sender and self.hist_texts[i]:
                subprocess.run(["pbcopy"], input=self.hist_texts[i], text=True)
                break

    def quick_add(self, _):
        text = self._show_add_dialog()
        if text is None:
            return
        line = text.strip().replace("＝", "=").replace("→", "=")
        if not line:
            return
        add_replacement(line) if "=" in line else add_phrase(line)
        self.status_item.title = "✓ 已加入，下次轉錄就套用"

    def _show_add_dialog(self):
        """自刻輸入視窗，靠拉高視窗層級 + orderFrontRegardless 強制顯示在最上層
        （背景選單列 App 在新版 macOS 不能搶焦點，這是正規做法）。回傳文字或 None。"""
        from AppKit import (
            NSAlert, NSTextField, NSApp, NSFloatingWindowLevel,
            NSAlertFirstButtonReturn,
        )
        alert = NSAlert.alloc().init()
        alert.setMessageText_("快速加詞／修正")
        alert.setInformativeText_(
            "輸入一個詞讓它認得；或用「錯＝對」修正常錯字（例：錯字＝正確字）"
        )
        alert.addButtonWithTitle_("加入")
        alert.addButtonWithTitle_("取消")
        field = NSTextField.alloc().initWithFrame_(((0, 0), (300, 24)))
        alert.setAccessoryView_(field)
        win = alert.window()
        win.setLevel_(NSFloatingWindowLevel)      # 浮在一般視窗之上
        NSApp.activateIgnoringOtherApps_(True)
        win.makeKeyAndOrderFront_(None)
        win.orderFrontRegardless()                # 背景 App 也能強制顯示
        win.makeFirstResponder_(field)            # 游標直接落在輸入框
        clicked = alert.runModal()
        return field.stringValue() if clicked == NSAlertFirstButtonReturn else None

    def edit_phrases(self, _):
        load_phrases()
        subprocess.run(["open", "-a", "TextEdit", PHRASES_FILE])

    def edit_replace(self, _):
        load_replacements()
        subprocess.run(["open", "-a", "TextEdit", REPLACE_FILE])

    # ---- 熱鍵（toggle）----
    def _on_flags(self, event):
        # 右 ⌘ 按下 → 開始/停止聽寫；右 ⌥ 按下 → 跳出快速加詞視窗
        try:
            kc, flags = event.keyCode(), event.modifierFlags()
            if kc == RIGHT_CMD_KEYCODE and (flags & CMD_FLAG):
                self.engine.toggle()
            elif kc == RIGHT_OPT_KEYCODE and (flags & OPT_FLAG):
                self.quick_add(None)
        except Exception as e:
            print(f"[err] 熱鍵處理：{e}")

    def _on_flags_local(self, event):
        self._on_flags(event)
        return event        # 本地監看必須回傳事件，否則會吞掉

    def quit(self, _):
        rumps.quit_application()


if __name__ == "__main__":
    VoiceApp().run()
