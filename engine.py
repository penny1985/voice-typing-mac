#!/usr/bin/env python3
"""
語音轉文字核心引擎。
被 menubar_app.py（選單列版）與 voice_typing.py（指令版）共用。
負責：toggle 錄音 → mlx-whisper 轉錄 → opencc 轉繁 → 規則整理 → 自動貼上 → 寫歷史。
"""

import os
import re
import sys
import time
import threading
import subprocess

import numpy as np
import sounddevice as sd
import mlx_whisper
from opencc import OpenCC
from pynput.keyboard import Key, Controller

# ============================================================
# 設定區（要改行為改這裡就好）
# ============================================================

# 按一下開始錄、再按一下停（toggle）。Mac 筆電沒有右 Ctrl，用右 ⌘ Command。
HOTKEY = Key.cmd_r

# Whisper 模型。large-v3-turbo 在 M 系列晶片上又快又準，中文表現好。
MODEL = "mlx-community/whisper-large-v3-turbo"

# 鎖定語言。"zh" = 中文。設成 None 則自動偵測（中英混說時用）。
LANGUAGE = "zh"

# 內建專有名詞提示（再加上 慣用語.txt 的內容）
INITIAL_PROMPT = "以下是繁體中文語音輸入。"

SAMPLE_RATE = 16000     # Whisper 要 16kHz
CHANNELS = 1            # 單聲道
MIN_DURATION = 0.3      # 太短忽略（誤觸保護），秒
MAX_DURATION = 1800     # 安全上限 30 分鐘，超過自動停止

# 檔案位置（跟程式放在同一個資料夾）
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PHRASES_FILE = os.path.join(BASE_DIR, "慣用語.txt")
REPLACE_FILE = os.path.join(BASE_DIR, "修正規則.txt")
HISTORY_KEEP = 2        # 最近紀錄留幾筆（只存記憶體，不寫硬碟）

# 講話停頓超過這秒數，視為換段落，自動分段（空行）
PARAGRAPH_GAP = 1.0
# 停頓介於這秒數與換段之間，補一個標點（短停頓）
PAUSE_GAP = 0.5
# 短停頓補的符號（要改逗號就換成 "，"）
PAUSE_PUNCT = "、"
# 已是這些結尾就不再補標點，避免重複
ENDING_PUNCT = "。！？，、；：、\n 　"
# 句子結尾標點：只有句子講完又停頓才換段落，避免講到一半被硬分
SENTENCE_END = "。！？"

# 半形標點 → 中文全形（只在中文字旁邊轉，避免破壞 3.14、v1.5）
PUNCT = {",": "，", ".": "。", "!": "！", "?": "？", ":": "：", ";": "；"}

# 慣用語檔第一次不存在時，幫你建一份預設
DEFAULT_PHRASES = """\
# 一行一個你常用的詞、人名、專有名詞，存檔後即時生效。
# 開頭是 # 的行會被忽略，當作說明。
閱讀塗鴉實驗室
陳沛孺
Penny
路老闆
AEO
SEO
GEO
PAYUNi
Claude Code
AI 應用講師
"""

# 修正規則第一次不存在時的預設
DEFAULT_REPLACE = """\
# 用「錯=對」格式，一行一條，存檔後即時生效。
# 例如它常把「路老闆」聽成「陸老闆」，就寫一行：陸老闆=路老闆
# 開頭是 # 的行會被忽略。
"""


def load_phrases():
    """讀 慣用語.txt，回傳要附加到提示的詞彙字串。檔案不存在就建預設。"""
    if not os.path.exists(PHRASES_FILE):
        with open(PHRASES_FILE, "w", encoding="utf-8") as f:
            f.write(DEFAULT_PHRASES)
    terms = []
    with open(PHRASES_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                terms.append(line)
    return ("常見詞彙：" + "、".join(terms) + "。") if terms else ""


def tidy(text):
    """本機規則整理：去語氣詞、半形標點轉中文全形、中英數補空格。不連網、不花錢。"""
    text = text.strip()
    if not text:
        return text
    text = re.sub(r"^[嗯呃欸啊]+[，,、。 \t]*", "", text)                     # 去開頭語氣詞
    text = re.sub(r"(\S+)(?:\s+\1){2,}", r"\1", text)                        # 收掉疊字幻覺（Penny Penny Penny→Penny）
    # 半形標點 → 全形（僅在中文字旁；只吃空格不吃換行，保住分段）
    text = re.sub(r"([一-鿿])[ \t]*([,.!?:;])", lambda m: m.group(1) + PUNCT[m.group(2)], text)
    text = re.sub(r"([,.!?:;])[ \t]*([一-鿿])", lambda m: PUNCT[m.group(1)] + m.group(2), text)
    text = re.sub(r"([一-鿿])([A-Za-z0-9])", r"\1 \2", text)              # 中→英數補空格
    text = re.sub(r"([A-Za-z0-9])([一-鿿])", r"\1 \2", text)              # 英數→中補空格
    text = re.sub(r"[ \t]{2,}", " ", text)                                   # 多空白收一個
    return text.strip()


def load_replacements():
    """讀 修正規則.txt，回傳 [(錯, 對), ...]。檔案不存在就建預設。"""
    if not os.path.exists(REPLACE_FILE):
        with open(REPLACE_FILE, "w", encoding="utf-8") as f:
            f.write(DEFAULT_REPLACE)
    rules = []
    with open(REPLACE_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            sep = "=" if "=" in line else ("→" if "→" in line else None)
            if sep:
                wrong, right = line.split(sep, 1)
                if wrong.strip():
                    rules.append((wrong.strip(), right.strip()))
    return rules


def apply_replacements(text):
    """套用 錯→對 修正，保證把常錯字換掉。"""
    for wrong, right in load_replacements():
        text = text.replace(wrong, right)
    return text


def add_phrase(term):
    """把一個詞加進 慣用語.txt（已存在就略過）。"""
    load_phrases()
    existing = open(PHRASES_FILE, encoding="utf-8").read()
    if term not in existing.split():
        with open(PHRASES_FILE, "a", encoding="utf-8") as f:
            f.write(term.strip() + "\n")


def add_replacement(line):
    """把一條『錯=對』規則加進 修正規則.txt。"""
    load_replacements()
    with open(REPLACE_FILE, "a", encoding="utf-8") as f:
        f.write(line.strip() + "\n")


def segments_to_text(result):
    """把 Whisper 的分段結果組成文字：停頓較久的地方自動分段（空行）。"""
    segs = result.get("segments") or []
    if not segs:
        return result.get("text", "").strip()
    out = ""
    prev_end = None
    for s in segs:
        t = (s.get("text") or "").strip()
        if not t:
            continue
        if prev_end is not None:
            gap = s.get("start", 0) - prev_end
            last = out[-1] if out else ""
            if gap > PARAGRAPH_GAP and last in SENTENCE_END:
                out += "\n\n"                              # 句子已結束 + 長停頓 → 換段落
            elif gap > PAUSE_GAP and out and last not in ENDING_PUNCT:
                out += PAUSE_PUNCT                          # 短停頓 → 補頓號
        out += t
        prev_end = s.get("end", prev_end)
    return out.strip()


class Engine:
    """toggle 錄音與轉錄引擎。透過 on_status 回呼通知介面狀態。"""

    def __init__(self, on_status=None):
        self.on_status = on_status or (lambda state, text=None: None)
        self.cc = OpenCC("s2twp")
        self.kb = Controller()
        self._frames = []
        self._stream = None
        self._recording = False
        self._busy = False
        self._safety_timer = None
        self.level = 0.0        # 即時音量（給波形指示器用），0~1

    def toggle(self):
        """按一下：沒在錄就開始錄；正在錄就停止並轉錄。"""
        if self._recording:
            self.stop()
        elif not self._busy:
            self.start()

    def _audio_callback(self, indata, frames, time_info, status):
        if status:
            print(f"[音訊警告] {status}", file=sys.stderr)
        self._frames.append(indata.copy())
        self.level = float(np.sqrt(np.mean(indata ** 2)))   # RMS 音量

    def start(self):
        if self._recording or self._busy:
            return
        self._frames = []
        self._recording = True
        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE, channels=CHANNELS,
            dtype="float32", callback=self._audio_callback,
        )
        self._stream.start()
        self._safety_timer = threading.Timer(MAX_DURATION, self._auto_stop)
        self._safety_timer.start()
        self.on_status("recording")

    def _auto_stop(self):
        if self._recording:
            print(f"[提醒] 已達 {MAX_DURATION // 60} 分鐘上限，自動停止錄音。")
            self.stop()

    def stop(self):
        if not self._recording:
            return
        self._recording = False
        if self._safety_timer:
            self._safety_timer.cancel()
            self._safety_timer = None
        self._stream.stop()
        self._stream.close()
        self._stream = None

        if not self._frames:
            self.on_status("idle")
            return
        audio = np.concatenate(self._frames, axis=0).flatten().astype(np.float32)
        if len(audio) / SAMPLE_RATE < MIN_DURATION:
            self.on_status("idle")
            return
        self._busy = True
        threading.Thread(target=self._transcribe_and_paste, args=(audio,), daemon=True).start()

    def _transcribe_and_paste(self, audio):
        try:
            self.on_status("transcribing")
            result = mlx_whisper.transcribe(
                audio,
                path_or_hf_repo=MODEL,
                language=LANGUAGE,
                initial_prompt=INITIAL_PROMPT + load_phrases(),
            )
            raw = segments_to_text(result)           # 依停頓自動分段
            text = tidy(self.cc.convert(raw))
            text = apply_replacements(text)          # 保證修正常錯字（錯→對）
            if not text:
                self.on_status("idle")
                return
            self._paste(text)
            self.on_status("done", text)
        except Exception as e:
            self.on_status("error", str(e))
            print(f"[轉錄失敗] {e}", file=sys.stderr)
        finally:
            self._busy = False

    def _paste(self, text):
        """暫存原剪貼簿 → 放入結果 → 模擬 Cmd+V → 還原剪貼簿。"""
        old_clip = subprocess.run(["pbpaste"], capture_output=True, text=True).stdout
        subprocess.run(["pbcopy"], input=text, text=True)
        time.sleep(0.1)
        self.kb.press(Key.cmd)
        self.kb.press("v")
        self.kb.release("v")
        self.kb.release(Key.cmd)
        time.sleep(0.2)
        subprocess.run(["pbcopy"], input=old_clip, text=True)
