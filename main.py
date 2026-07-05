import os
import sys
import time
import random
import html
import json
import requests
import tempfile
import subprocess
import signal
import base64
from datetime import datetime, timezone, timedelta
from xvfbwrapper import Xvfb
from DrissionPage import ChromiumPage, ChromiumOptions

try:
    import speech_recognition as sr
    from pydub import AudioSegment
except ImportError:
    pass

MAX_CAPTCHA = 3
LOCAL_PROXY = "http://127.0.0.1:8080"

class CaptchaBlocked(Exception):
    pass

def log(msg, level="INFO"):
    prefix = {"INFO": "[INFO]", "WARN": "[WARN]", "ERROR": "[ERROR]"}.get(level, "[INFO]")
    print(f"{prefix} {msg}", flush=True)

def send_tg_photo(tg_tc, photo_path, caption, parse_mode='HTML'):
    if not tg_tc or " " not in tg_tc:
        log("未配置 TG_TC 或格式错误(应为 'Token ChatID')，跳过通知。", "WARN")
        return
    
    token, chat_id = tg_tc.split(" ", 1)
    if not photo_path or not os.path.exists(photo_path):
        log("未找到截图文件，跳过通知。", "WARN")
        return
    
    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    try:
        with open(photo_path, "rb") as photo_file:
            response = requests.post(
                url,
                data={"chat_id": chat_id, "caption": caption, "parse_mode": parse_mode},
                files={"photo": photo_file},
                timeout=30,
            )
        response.raise_for_status()
        log("Telegram 图片通知发送成功")
    except Exception as e:
        log(f"Telegram 图片通知异常: {e}", "ERROR")

def build_notification(success, account_id, url, server_name, old_expire, new_expire=None, route_type="未知", failure_reason=""):
    """构建精美的 HTML 格式 TG 通知"""
    server_name_safe = html.escape(server_name)
    url_safe = html.escape(url)
    
    if success:
        caption = f"""🎉 <b>Host2Play 续期成功</b> 🎉
━━━━━━━━━━━━━━━━━━
👤 <b>账号标识:</b> <code>{account_id}</code>
🖥 <b>节点名称:</b> {server_name_safe}
🌐 <b>通信路由:</b> {route_type}
⏳ <b>到期时间:</b> {html.escape(old_expire)} ➔ <b>{html.escape(new_expire)}</b>
🔗 <a href="{url_safe}">进入面板直达链接</a>
━━━━━━━━━━━━━━━━━━
<i>Host2Play Auto Renew Bot</i>"""
    else:
        fail_safe = html.escape(failure_reason)
        caption = f"""⚠️ <b>Host2Play 续期失败</b> ⚠️
━━━━━━━━━━━━━━━━━━
👤 <b>账号标识:</b> <code>{account_id}</code>
🖥 <b>节点名称:</b> {server_name_safe}
🌐 <b>通信路由:</b> {route_type}
🛑 <b>失败原因:</b> <code>{fail_safe}</code>
🔗 <a href="{url_safe}">查看节点链接配置</a>
━━━━━━━━━━━━━━━━━━
<i>Host2Play Auto Renew Bot</i>"""
    return caption

def fetch_subscription(url):
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        text = r.text.strip()
        
        if "://" in text:
            return [line.strip() for line in text.splitlines() if "://" in line]
        
        text = text.replace("-", "+").replace("_", "/")
        pad = 4 - len(text) % 4
        if pad != 4: 
            text += "=" * pad
        decoded = base64.b64decode(text).decode("utf-8")
        return [line.strip() for line in decoded.splitlines() if "://" in line]
    except Exception as e:
        log(f"获取或解析订阅链接失败: {e}", "ERROR")
        return []

def get_server_name(page):
    try:
        if ele := page.ele('#serverName', timeout=2): return ele.text.strip()
    except: pass
    return "未知"

def get_expire_time(page):
    try:
        if ele := page.ele('#expireDate', timeout=2): return ele.text.strip()
    except: pass
    for selector in ['text:Expires in:', 'text:Deletes on:']:
        try:
            if ele := page.ele(selector, timeout=1):
                text = (ele.text or "").strip()
                return text.split(":", 1)[1].strip() if ":" in text else text
        except: pass
    return "未知"

def capture_page_screenshot(page, file_name):
    try:
        page.get_screenshot(path=file_name)
        return file_name
    except Exception as e:
        log(f"截图失败: {e}", "WARN")
        return None

def start_singbox(proxy_url):
    log("尝试启动自定义代理 (sing-box)...")
    try:
        os.environ["PROXY_URL"] = proxy_url
        subprocess.run([sys.executable, "proxyurl.py"], check=True)
        proc = subprocess.Popen(["sing-box", "run", "-c", "config.json"], 
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(3) 
        log("自定义代理启动成功 (127.0.0.1:8080)")
        return proc
    except Exception as e:
        log(f"启动自定义代理失败: {e}", "ERROR")
        return None

def stop_singbox(proc):
    if proc:
        log("关闭自定义代理 (sing-box)...")
        try:
            os.kill(proc.pid, signal.SIGTERM)
            proc.wait(timeout=3)
        except Exception:
            try: proc.kill()
            except: pass

def restart_warp():
    log("正在重启 WARP 以更换 IP...")
    try:
        subprocess.run(["sudo", "warp-cli", "--accept-tos", "disconnect"], check=False, timeout=15, capture_output=True)
        time.sleep(2)
        try: subprocess.run(["sudo", "warp-cli", "--accept-tos", "registration", "delete"], check=True, timeout=15, capture_output=True)
        except: pass
        subprocess.run(["sudo", "warp-cli", "--accept-tos", "registration", "new"], check=True, timeout=15, capture_output=True)
        time.sleep(2)
        subprocess.run(["sudo", "warp-cli", "--accept-tos", "connect"], check=True, timeout=15, capture_output=True)
        time.sleep(5)
        log("WARP 重连完成。")
        return True
    except Exception as e:
        log(f"WARP 重连失败: {e}", "ERROR")
        return False

def find_recaptcha_frame(page, kind):
    try:
        for frame in page.get_frames():
            if "recaptcha" in (frame.url or "") and kind in (frame.url or ""):
                return frame
    except: pass
    return None

def is_recaptcha_solved(page):
    try:
        for frame in page.get_frames():
            try:
                token = frame.run_js("return document.querySelector(\"textarea[name='g-recaptcha-response']\")?.value")
                if token and len(token) > 30: return True
            except: pass
    except: pass
    if anchor := find_recaptcha_frame(page, "anchor"):
        try: return anchor.run_js("return document.querySelector('#recaptcha-anchor')?.getAttribute('aria-checked') === 'true'")
        except: pass
    return False

def is_blocked(page):
    if not (bframe := find_recaptcha_frame(page, "bframe")): return False
    try:
        return bool(bframe.run_js("""
            const h = document.querySelector('.rc-doscaptcha-header-text');
            if (h && h.textContent.toLowerCase().includes('try again later')) return true;
            const e = document.querySelector('.rc-audiochallenge-error-message');
            if (e && e.offsetParent !== null) return true;
            return false;
        """))
    except: return False

def click_recaptcha_checkbox(page):
    if not (anchor := find_recaptcha_frame(page, "anchor")): raise RuntimeError("未找到 reCAPTCHA anchor frame")
    if not (checkbox := anchor.ele('#recaptcha-anchor', timeout=3)): raise RuntimeError("未找到 reCAPTCHA 复选框")
    
    page.actions.move_to(checkbox, duration=random.uniform(0.4, 1.0))
    time.sleep(random.uniform(0.2, 0.5))
    try: checkbox.click()
    except: checkbox.click(by_js=True)
    
    time.sleep(3)
    if is_blocked(page): raise CaptchaBlocked("点击复选框后检测到 IP 被封锁")

def switch_to_audio(page):
    if not (bframe := find_recaptcha_frame(page, "bframe")): return False
    for _ in range(3):
        try:
            if audio_btn := bframe.ele('#recaptcha-audio-button', timeout=3):
                try: audio_btn.click()
                except: audio_btn.click(by_js=True)
                time.sleep(3)
                if is_blocked(page): raise CaptchaBlocked("点击音频按钮后检测到 IP 被封锁")
                if bframe.ele('#audio-response', timeout=1): return True
        except CaptchaBlocked: raise
        except: pass
        time.sleep(2)
    return False

def get_audio_url(page):
    if not (bframe := find_recaptcha_frame(page, "bframe")): return None
    for _ in range(5):
        try:
            for selector in ['.rc-audiochallenge-tdownload-link', '.rc-audiochallenge-ndownload-link', '#audio-source']:
                if ele := bframe.ele(selector, timeout=1):
                    url = ele.attr('href') or ele.attr('src')
                    if url and len(url) > 10: return html.unescape(url)
        except: pass
        time.sleep(1)
    return None

def download_audio(url, use_proxy):
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    proxies = {"http": LOCAL_PROXY, "https": LOCAL_PROXY} if use_proxy else None
    
    urls = [url]
    if "recaptcha.net" in url: urls.append(url.replace("recaptcha.net", "www.google.com"))
    elif "google.com" in url: urls.append(url.replace("www.google.com", "recaptcha.net"))
    
    for audio_url in urls:
        try:
            r = requests.get(audio_url, headers=headers, proxies=proxies, timeout=15)
            r.raise_for_status()
            if len(r.content) > 1000:
                path = tempfile.mktemp(suffix=".mp3")
                with open(path, "wb") as f: f.write(r.content)
                return path
        except: pass
    return None

def recognize_audio(mp3_path):
    try:
        wav_path = mp3_path.replace(".mp3", ".wav")
        AudioSegment.from_mp3(mp3_path).export(wav_path, format="wav")
        recognizer = sr.Recognizer()
        with sr.AudioFile(wav_path) as src:
            text = recognizer.recognize_google(recognizer.record(src))
        try: os.remove(wav_path)
        except: pass
        return text
    except: return None

def solve_recaptcha(page, use_proxy):
    start = time.time()
    while time.time() - start < 15:
        if find_recaptcha_frame(page, "anchor"): break
        time.sleep(1)
    else: raise RuntimeError("reCAPTCHA 加载超时")

    for i in range(MAX_CAPTCHA):
        if is_recaptcha_solved(page): return True
        if is_blocked(page): raise CaptchaBlocked("IP 被封锁")

        if i == 0:
            click_recaptcha_checkbox(page)
            time.sleep(2)
            if is_recaptcha_solved(page): return True

        if not switch_to_audio(page):
            click_recaptcha_checkbox(page)
            time.sleep(3)
            continue

        if not (audio_url := get_audio_url(page)): continue
        if not (mp3 := download_audio(audio_url, use_proxy)): continue
        
        text = recognize_audio(mp3)
        try: os.remove(mp3)
        except: pass
        if not text: continue

        log(f"音频识别结果: [{text}]")
        bframe = find_recaptcha_frame(page, "bframe")
        if input_box := bframe.ele('#audio-response', timeout=2):
            input_box.click()
            input_box.clear()
            input_box.input(text)
            time.sleep(1)
            if verify_btn := bframe.ele('#recaptcha-verify-button', timeout=2):
                verify_btn.click(by_js=True)
            
        time.sleep(5)
        if is_recaptcha_solved(page): return True
        time.sleep(2)
    raise RuntimeError("验证码达到最大尝试次数")

def execute_browser_task(acc_id, url, using_custom_proxy, current_route):
    screenshot_dir = "output/screenshots"
    os.makedirs(screenshot_dir, exist_ok=True)
    
    success, server_name, old_expire, new_expire, failure_reason = False, "未知", "未知", "未知", ""
    screenshot_path = None
    page = None
    
    try:
        co = ChromiumOptions()
        co.set_browser_path('/usr/bin/google-chrome')
        co.set_argument('--no-sandbox')
        co.set_argument('--disable-gpu')
        co.set_argument('--window-size=1280,720')
        co.set_argument('--log-level=3')
        
        if using_custom_proxy: 
            co.set_argument(f'--proxy-server={LOCAL_PROXY}')
            
        co.set_user_data_path(tempfile.mkdtemp())
        co.auto_port()
        co.headless(False)
        page = ChromiumPage(co)

        page.add_init_js("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            WebGLRenderingContext.prototype.getParameter = function(p) { return p === 37446 ? 'Intel(R) UHD Graphics 630' : 1; };
        """)

        page.get(url, retry=3)
        time.sleep(random.uniform(5, 8))

        server_name = get_server_name(page)
        old_expire = get_expire_time(page)
        log(f"节点: {server_name}, 当前到期: {old_expire}, 当前路由: {current_route}")

        page.run_js("document.querySelectorAll('ins.adsbygoogle, .modal-backdrop').forEach(e => e.remove());")
        if consent := page.ele('tag:button@@text():Consent', timeout=2): consent.click()
        time.sleep(2)

        if renew_btn1 := page.ele('xpath://button[contains(text(), "Renew server")]', timeout=3): renew_btn1.click(by_js=True)
        time.sleep(3)

        page.ele('text:Expires in:', timeout=8)
        if renew_btn2 := page.ele('xpath://button[contains(text(), "Renew server")]', timeout=2): renew_btn2.click(by_js=True)
        time.sleep(8)

        if find_recaptcha_frame(page, "anchor"):
            log("启动 reCAPTCHA 破解...")
            solve_recaptcha(page, using_custom_proxy) # 如被封禁将抛出 CaptchaBlocked
        
        if final_btn := page.ele('xpath://button[normalize-space(text())="Renew"]', timeout=3):
            final_btn.click(by_js=True)
            time.sleep(10)
            new_expire = get_expire_time(page)
            if new_expire != old_expire and new_expire != "未知": 
                success = True
            elif "successfully" in (page.html or "").lower(): 
                success = True
            else: 
                failure_reason = "未能检测到成功标志"
        else:
            failure_reason = "未找到最终 Renew 按钮"

    except CaptchaBlocked:
        log("IP 被封锁！", "WARN")
        failure_reason = "CaptchaBlocked: IP被封锁"
    except Exception as e:
        log(f"尝试异常: {e}", "ERROR")
        failure_reason = str(e)[:200]
    finally:
        if page:
            screen_name = f"account-{acc_id}-{'success' if success else 'fail'}.png"
            screenshot_path = capture_page_screenshot(page, os.path.join(screenshot_dir, screen_name))
            try: page.quit()
            except: pass
            
    return success, server_name, old_expire, new_expire, screenshot_path, failure_reason

def process_account(account):
    acc_id = account['id']
    url = account['url']
    proxy_url = account['proxy']
    proxy_link = account.get('proxy_link', '')
    
    success, server_name, old_expire, new_expire, failure_reason = False, "未知", "未知", "未知", ""
    screenshot_path = None
    final_route_type = "未执行"

    vdisplay = Xvfb(width=1280, height=720, colordepth=24)
    vdisplay.start()
    
    try:
        proxies_to_try = []
        if proxy_url:
            proxies_to_try.append(proxy_url)
        if proxy_link:
            log(f"账号 {acc_id} 配置了订阅链接，正在获取解析...")
            subs = fetch_subscription(proxy_link)
            if subs:
                log(f"解析到 {len(subs)} 个有效代理节点。")
                proxies_to_try.extend(subs)
            else:
                log("未解析出任何代理节点或订阅链接无效", "WARN")

        # 2. 按顺序执行每个自定义代理节点
        for p_idx, p in enumerate(proxies_to_try):
            if success: break
            
            # 单代理尝试固定为 3 次
            for attempt in range(1, 4):
                current_route = f"代理节点 {p_idx+1} (Sing-box)"
                log(f"--- 账号 {acc_id} | {current_route} | 续期尝试 {attempt}/3 ---")
                
                singbox_proc = start_singbox(p)
                if not singbox_proc:
                    log("自定义代理启动失败，跳过此次重试")
                    continue
                    
                s, s_name, o_exp, n_exp, screen, reason = execute_browser_task(acc_id, url, True, current_route)
                stop_singbox(singbox_proc)
                
                success, server_name, old_expire, new_expire = s, s_name, o_exp, n_exp
                screenshot_path, failure_reason, final_route_type = screen, reason, current_route
                
                if success:
                    break
                    
                if "IP被封锁" in failure_reason:
                    log("当前代理IP已被验证码封锁，终止当前代理其余尝试，更换下一个节点", "WARN")
                    break  # IP已被封禁，尝试下个节点

        # 3. 如果所有代理节点尝试均失败或未配置任何代理，执行 WARP 保底
        if not success:
            log("代理节点全部失败或未配置，进入 WARP 兜底模式...")
            for attempt in range(1, 6):
                current_route = "全局 WARP"
                log(f"--- 账号 {acc_id} | {current_route} 兜底 | 续期尝试 {attempt}/5 ---")
                
                restart_warp()
                
                s, s_name, o_exp, n_exp, screen, reason = execute_browser_task(acc_id, url, False, current_route)
                success, server_name, old_expire, new_expire = s, s_name, o_exp, n_exp
                screenshot_path, failure_reason, final_route_type = screen, reason, current_route
                
                if success:
                    break

    finally:
        vdisplay.stop()

    return success, server_name, old_expire, new_expire, screenshot_path, failure_reason, final_route_type


def main():
    secrets_json_str = os.getenv("ALL_SECRETS", "{}")
    try:
        injected_secrets = json.loads(secrets_json_str)
    except Exception:
        injected_secrets = {}
        
    env_vars = {**os.environ, **injected_secrets}
    tg_tc = env_vars.get("TG_TC", "").strip()

    raw_accounts = {}
    for key, value in env_vars.items():
        val_str = str(value).strip()
        if not val_str: continue
        
        if key.startswith("RENEW_URLS_"):
            suffix = key.replace("RENEW_URLS_", "")
            if suffix not in raw_accounts: raw_accounts[suffix] = {"id": suffix, "url": "", "proxy": "", "proxy_link": ""}
            raw_accounts[suffix]["url"] = val_str
            
        elif key.startswith("PROXY_URL_"):
            suffix = key.replace("PROXY_URL_", "")
            if suffix not in raw_accounts: raw_accounts[suffix] = {"id": suffix, "url": "", "proxy": "", "proxy_link": ""}
            raw_accounts[suffix]["proxy"] = val_str

        elif key.startswith("PROXY_LINK_"):
            suffix = key.replace("PROXY_LINK_", "")
            if suffix not in raw_accounts: raw_accounts[suffix] = {"id": suffix, "url": "", "proxy": "", "proxy_link": ""}
            raw_accounts[suffix]["proxy_link"] = val_str

    accounts = [acc for acc in raw_accounts.values() if acc["url"]]
    
    if not accounts:
        log("未检测到任何有效的 RENEW_URLS_X 环境变量，脚本退出。", "ERROR")
        sys.exit(1)
        
    def sort_key(x):
        try: return (0, int(x["id"]))
        except ValueError: return (1, x["id"])
    accounts.sort(key=sort_key)

    total_success = 0
    for acc in accounts:
        has_proxy = bool(acc['proxy'] or acc.get('proxy_link'))
        log(f"\n{'='*60}\n开始处理账号 {acc['id']} (代理: {'已配置' if has_proxy else '未配置'})\n{'='*60}")
        
        success, server_name, old_expire, new_expire, screenshot, fail_reason, route_type = process_account(acc)

        if success: total_success += 1
        caption = build_notification(success, acc['id'], acc['url'], server_name, old_expire, new_expire, route_type, fail_reason)
        send_tg_photo(tg_tc, screenshot, caption)

    log(f"\n全部完成，成功 {total_success}/{len(accounts)} 个账号")
    if total_success < len(accounts): sys.exit(1)

if __name__ == "__main__":
    main()
