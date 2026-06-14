-- 這是啟動器範本，僅供參考。
-- 實際的「語音輸入.app」是由 build_app.sh 依你的安裝路徑自動產生，
-- 直接執行  bash build_app.sh  即可，不需手動改這個檔。
with timeout of 315360000 seconds
	do shell script "<安裝資料夾>/venv/bin/python <安裝資料夾>/menubar_app.py > /tmp/voice-typing.log 2>&1"
end timeout
