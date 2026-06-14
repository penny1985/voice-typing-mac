#!/usr/bin/env python3
"""
指令版語音輸入（在終端機顯示狀態）。
一般日常使用請開選單列版 App（menubar_app.py / 語音輸入.app）。
這個版本適合除錯，能在終端機看到完整訊息。
"""

from pynput import keyboard

from engine import Engine, HOTKEY, MODEL


def on_status(state, text=None):
    msgs = {
        "recording": "\n● 錄音中…（放開熱鍵結束）",
        "transcribing": "⠿ 轉錄中…",
        "idle": "（已取消）",
    }
    if state == "done":
        print(f"✓ {text}")
    elif state == "error":
        print(f"[錯誤] {text}")
    elif state in msgs:
        print(msgs[state])


def main():
    engine = Engine(on_status=on_status)
    print("=" * 48)
    print("  本機語音輸入（指令版）已啟動")
    print("  按一下 [右 ⌘ Command] 開始錄，再按一下停止轉錄貼上")
    print(f"  模型：{MODEL}")
    print("  結束請按 Ctrl+C")
    print("=" * 48)
    print("（第一次執行會下載模型約 1.5GB，請耐心等）")

    def on_press(key):
        if key == HOTKEY:
            engine.toggle()

    with keyboard.Listener(on_press=on_press) as listener:
        listener.join()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n已結束。")
