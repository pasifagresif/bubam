#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import os
import shutil
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from tkinter.scrolledtext import ScrolledText

# Çalışma dizinini tespit et.
# PyInstaller ile tek-dosya (.exe) olarak paketlendiğinde __file__, program
# kapanınca silinen geçici bir açılış klasörünü gösterir (sys._MEIPASS).
# Bu yüzden exe'nin GERÇEK konumunu sys.executable'dan almalıyız; aksi halde
# indirilen videolar temp klasörüne düşer ve program kapanınca kaybolur.
if getattr(sys, "frozen", False):
    workspace_dir = os.path.dirname(os.path.abspath(sys.executable))
else:
    workspace_dir = os.path.dirname(os.path.abspath(__file__))

# PyInstaller tek-dosya modunda paketlenmiş binary'ler (ffmpeg.exe/ffprobe.exe)
# exe'nin yanına değil, çalışma anında açılan geçici klasöre (sys._MEIPASS)
# çıkarılır. Bu yüzden "yerel ffmpeg nerede" sorusunun cevabı frozen'da farklıdır.
bundled_ffmpeg_dir = getattr(sys, "_MEIPASS", workspace_dir)

# Sistemde yüklü bir ffmpeg olup olmadığını kontrol et
system_ffmpeg = shutil.which("ffmpeg")

# Eğer sistemde halihazırda yüklü bir ffmpeg varsa onu kullan (segmentation fault yaşamamak için).
# Eğer sistemde yoksa, programla birlikte gelen statik sürümü PATH'e ekle (yedek plan).
if system_ffmpeg and not system_ffmpeg.startswith(bundled_ffmpeg_dir):
    print(f"Sistemdeki FFmpeg bulundu ve kullanılacak: {system_ffmpeg}")
else:
    print("Sistemde FFmpeg bulunamadı, programla gelen statik sürüm PATH'e ekleniyor.")
    os.environ["PATH"] = bundled_ffmpeg_dir + os.path.pathsep + os.environ.get("PATH", "")

# yt-dlp'yi önce normal (pip ile kurulu) paket olarak almayı dene.
# Bu, hem PyInstaller ile paketlerken hem de geliştirirken tutarlı çalışır;
# PyInstaller donmuş (frozen) haldeyken vendored kaynak klasörü zaten yanında
# gelmez, o yüzden ona güvenmek exe'de import hatasına yol açar.
try:
    import yt_dlp
    from yt_dlp.utils import download_range_func
except ImportError:
    if not getattr(sys, "frozen", False):
        # Geliştirme ortamında pip kurulu değilse, yanındaki kaynak klasörüne düş
        yt_dlp_path = os.path.join(workspace_dir, "yt-dlp")
        if yt_dlp_path not in sys.path:
            sys.path.insert(0, yt_dlp_path)
        import yt_dlp
        from yt_dlp.utils import download_range_func
    else:
        raise

# Uygulamanın açık olup olmadığını takip eden bayrak
is_running = True

class MyLogger:
    def __init__(self, text_widget, root):
        self.text_widget = text_widget
        self.root = root

    def debug(self, msg):
        # Sadece kritik indirme/kesme çıktılarını log alanına yaz
        if any(keyword in msg for keyword in ["[download]", "[ExtractAudio]", "[Merger]", "[VideoConvertor]", "[ffmpeg]", "Destination"]):
            self._write(msg)

    def warning(self, msg):
        # Babayı endişelendirebilecek gereksiz JavaScript uyarılarını filtrele
        if "No supported JavaScript runtime could be found" not in msg:
            self._write(f"⚠️ UYARI: {msg}")

    def error(self, msg):
        self._write(f"❌ HATA: {msg}")

    def _write(self, msg):
        print(msg) # Konsola da yaz
        if is_running:
            try:
                self.root.after(0, self._append_text, msg + "\n")
            except Exception:
                pass

    def _append_text(self, text):
        if is_running:
            try:
                self.text_widget.config(state='normal')
                self.text_widget.insert('end', text)
                self.text_widget.see('end')
                self.text_widget.config(state='disabled')
            except Exception:
                pass

def my_hook(d):
    if not is_running:
        return
        
    if d['status'] == 'downloading':
        # İndirme yüzdesini ve hızını al
        percent_str = d.get('_percent_str', '').strip()
        speed = d.get('_speed_str', '').strip()
        eta = d.get('_eta_str', '').strip()
        
        status_msg = f"İndiriliyor: {percent_str} | Hız: {speed} | Kalan Süre: {eta}"
        try:
            root.after(0, status_label.config, {"text": status_msg})
        except Exception:
            pass
        
        # İlerleme çubuğunu güncelle
        try:
            val_str = percent_str.replace('%', '')
            val = float(val_str)
            root.after(0, progress_bar.config, {"value": val})
        except Exception:
            pass
            
    elif d['status'] == 'finished':
        try:
            root.after(0, status_label.config, {"text": "İndirme bitti. Ses ve video birleştiriliyor/kesiliyor..."})
            root.after(0, progress_bar.config, {"value": 100})
        except Exception:
            pass

def start_download_thread():
    url = url_entry.get().strip()
    if not url:
        messagebox.showerror("Hata", "Lütfen geçerli bir YouTube video linki girin!")
        return

    # Zaman girdilerini oku ve doğrula
    try:
        start_min = int(start_min_entry.get().strip() or 0)
        start_sec = int(start_sec_entry.get().strip() or 0)
        end_min = int(end_min_entry.get().strip() or 0)
        end_sec = int(end_sec_entry.get().strip() or 0)
    except ValueError:
        messagebox.showerror("Hata", "Dakika ve saniye alanlarına sadece sayı girmelisiniz!")
        return

    start_total_sec = start_min * 60 + start_sec
    end_total_sec = end_min * 60 + end_sec

    if end_total_sec > 0 and end_total_sec <= start_total_sec:
        messagebox.showerror("Hata", "Bitiş zamanı, başlangıç zamanından sonra olmalıdır!")
        return

    # Arayüz elemanlarını kilitle
    download_btn.config(state='disabled', bg='#383E56', text="İşlem Yapılıyor...")
    progress_bar.config(value=0)
    
    # İndirme işlemini arka planda çalıştır (Arayüz donmasın)
    download_thread = threading.Thread(
        target=run_download,
        args=(url, start_total_sec, end_total_sec),
        daemon=True
    )
    download_thread.start()

def run_download(url, start_sec, end_sec):
    if not is_running:
        return
        
    # Log alanını temizle
    try:
        root.after(0, clear_log)
        root.after(0, status_label.config, {"text": "Video bilgileri alınıyor..."})
    except Exception:
        pass

    ydl_opts = {
        'format': 'bestvideo+bestaudio/best',
        'merge_output_format': 'mp4',
        'outtmpl': os.path.join(workspace_dir, '%(title)s.%(ext)s'),
        'logger': MyLogger(log_text, root),
        'progress_hooks': [my_hook],
        'nocheckcertificate': True,
    }

    # Eğer sistemde ffmpeg yoksa ve programla gelen statik sürümü kullanıyorsak ydl_opts içine konumunu belirt
    if not system_ffmpeg:
        ydl_opts['ffmpeg_location'] = bundled_ffmpeg_dir

    # Kesme işlemi gerekiyorsa aralık belirle
    if start_sec > 0 or end_sec > 0:
        end_val = end_sec if end_sec > 0 else None
        ydl_opts['download_ranges'] = download_range_func(None, [(start_sec, end_val)])
        ydl_opts['force_keyframes_at_cuts'] = True

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        
        # Tamamlandı bildirimi
        if is_running:
            root.after(0, finish_download, True, "Video başarıyla indirildi ve kesildi!")
    except Exception as e:
        if is_running:
            root.after(0, finish_download, False, str(e))

def clear_log():
    if is_running:
        try:
            log_text.config(state='normal')
            log_text.delete('1.0', 'end')
            log_text.config(state='disabled')
        except Exception:
            pass

def finish_download(success, msg):
    if not is_running:
        return
    try:
        download_btn.config(state='normal', bg='#7AA2F7', text="📥 VİDEOYU İNDİR VE KES")
        if success:
            status_label.config(text="Tamamlandı! 🎉", fg="#9ECE6A")
            messagebox.showinfo("Başarılı", "Video indirme ve kesme işlemi başarıyla tamamlandı!\nDosya bu klasöre kaydedildi.")
        else:
            status_label.config(text="Hata oluştu! ❌", fg="#F7768E")
            messagebox.showerror("Hata", f"İşlem sırasında bir hata oluştu:\n\n{msg}")
    except Exception:
        pass

def on_closing():
    global is_running
    is_running = False
    try:
        root.destroy()
    except Exception:
        pass

# GUI Arayüz Tasarımı (Tokyo Night Dark Theme)
root = tk.Tk()
root.title("YouTube Kolay Video İndirici & Kesici")
root.geometry("680x640")
root.configure(bg="#1A1B26")
root.resizable(True, True)
root.protocol("WM_DELETE_WINDOW", on_closing)

# Grid konfigürasyonu (Pencere genişletildiğinde ortalansın)
root.columnconfigure(0, weight=1)

# Başlık Bölümü
title_label = tk.Label(
    root, 
    text="🎥 YouTube Kolay Video İndirici & Kesici", 
    font=("Helvetica", 16, "bold"), 
    bg="#1A1B26", 
    fg="#7AA2F7"
)
title_label.pack(pady=(20, 5))

subtitle_label = tk.Label(
    root, 
    text="Babam için özel ve sade tasarım ❤️", 
    font=("Helvetica", 10, "italic"), 
    bg="#1A1B26", 
    fg="#565F89"
)
subtitle_label.pack(pady=(0, 15))

# Link Giriş Çerçevesi
url_frame = tk.Frame(root, bg="#1A1B26")
url_frame.pack(fill="x", padx=25, pady=5)

url_label = tk.Label(
    url_frame, 
    text="YouTube Video Linki (Kopyalayıp Yapıştırın):", 
    font=("Helvetica", 11, "bold"), 
    bg="#1A1B26", 
    fg="#A9B1D6"
)
url_label.pack(anchor="w", pady=(0, 5))

url_entry = tk.Entry(
    url_frame, 
    font=("Helvetica", 12), 
    bg="#24283B", 
    fg="#C0CAF5", 
    bd=0, 
    highlightthickness=1, 
    highlightbackground="#383E56", 
    highlightcolor="#7AA2F7", 
    insertbackground="white"
)
url_entry.pack(fill="x", ipady=8)

# Zaman Belirleme Çerçevesi (Yan yana iki kutu)
time_frame = tk.Frame(root, bg="#1A1B26")
time_frame.pack(fill="x", padx=25, pady=15)
time_frame.columnconfigure(0, weight=1)
time_frame.columnconfigure(1, weight=1)

# Başlangıç Zamanı Kutusu
start_lf = tk.LabelFrame(
    time_frame, 
    text=" 🟢 Başlangıç Noktası (Kırpmanın Başlayacağı Yer) ", 
    font=("Helvetica", 10, "bold"), 
    bg="#1A1B26", 
    fg="#9ECE6A", 
    bd=1, 
    relief="solid", 
    padx=15, 
    pady=10
)
start_lf.grid(row=0, column=0, padx=(0, 10), sticky="nsew")

start_min_label = tk.Label(start_lf, text="Dakika:", font=("Helvetica", 10), bg="#1A1B26", fg="#A9B1D6")
start_min_label.grid(row=0, column=0, padx=5, pady=5, sticky="w")
start_min_entry = tk.Entry(
    start_lf, 
    font=("Helvetica", 11, "bold"), 
    bg="#24283B", 
    fg="#C0CAF5", 
    bd=0, 
    highlightthickness=1, 
    highlightbackground="#383E56", 
    highlightcolor="#9ECE6A", 
    insertbackground="white", 
    width=6, 
    justify="center"
)
start_min_entry.insert(0, "0")
start_min_entry.grid(row=0, column=1, padx=5, pady=5)

start_sec_label = tk.Label(start_lf, text="Saniye:", font=("Helvetica", 10), bg="#1A1B26", fg="#A9B1D6")
start_sec_label.grid(row=0, column=2, padx=5, pady=5, sticky="w")
start_sec_entry = tk.Entry(
    start_lf, 
    font=("Helvetica", 11, "bold"), 
    bg="#24283B", 
    fg="#C0CAF5", 
    bd=0, 
    highlightthickness=1, 
    highlightbackground="#383E56", 
    highlightcolor="#9ECE6A", 
    insertbackground="white", 
    width=6, 
    justify="center"
)
start_sec_entry.insert(0, "0")
start_sec_entry.grid(row=0, column=3, padx=5, pady=5)

# Bitiş Zamanı Kutusu
end_lf = tk.LabelFrame(
    time_frame, 
    text=" 🔴 Bitiş Noktası (Kırpmanın Biteceği Yer) ", 
    font=("Helvetica", 10, "bold"), 
    bg="#1A1B26", 
    fg="#F7768E", 
    bd=1, 
    relief="solid", 
    padx=15, 
    pady=10
)
end_lf.grid(row=0, column=1, padx=(10, 0), sticky="nsew")

end_min_label = tk.Label(end_lf, text="Dakika:", font=("Helvetica", 10), bg="#1A1B26", fg="#A9B1D6")
end_min_label.grid(row=0, column=0, padx=5, pady=5, sticky="w")
end_min_entry = tk.Entry(
    end_lf, 
    font=("Helvetica", 11, "bold"), 
    bg="#24283B", 
    fg="#C0CAF5", 
    bd=0, 
    highlightthickness=1, 
    highlightbackground="#383E56", 
    highlightcolor="#F7768E", 
    insertbackground="white", 
    width=6, 
    justify="center"
)
end_min_entry.insert(0, "0")
end_min_entry.grid(row=0, column=1, padx=5, pady=5)

end_sec_label = tk.Label(end_lf, text="Saniye:", font=("Helvetica", 10), bg="#1A1B26", fg="#A9B1D6")
end_sec_label.grid(row=0, column=2, padx=5, pady=5, sticky="w")
end_sec_entry = tk.Entry(
    end_lf, 
    font=("Helvetica", 11, "bold"), 
    bg="#24283B", 
    fg="#C0CAF5", 
    bd=0, 
    highlightthickness=1, 
    highlightbackground="#383E56", 
    highlightcolor="#F7768E", 
    insertbackground="white", 
    width=6, 
    justify="center"
)
end_sec_entry.insert(0, "0")
end_sec_entry.grid(row=0, column=3, padx=5, pady=5)

# Bilgilendirme İpucu
info_label = tk.Label(
    root, 
    text="💡 İpucu: Videoyu kesmeden tamamını indirmek için Başlangıç ve Bitiş değerlerini 0 saniye olarak bırakın.\nStart süresinden sonuna kadar indirmek için sadece Başlangıç girin, Bitiş değerlerini 0 bırakın.", 
    font=("Helvetica", 9), 
    bg="#1A1B26", 
    fg="#565F89", 
    justify="left"
)
info_label.pack(fill="x", padx=25, pady=(0, 10))

# İndirme Butonu
download_btn = tk.Button(
    root, 
    text="📥 VİDEOYU İNDİR VE KES", 
    font=("Helvetica", 12, "bold"), 
    bg="#7AA2F7", 
    fg="#1A1B26", 
    activebackground="#89B4FA", 
    activeforeground="#1A1B26", 
    bd=0, 
    cursor="hand2", 
    padx=20, 
    pady=12, 
    command=start_download_thread
)
download_btn.pack(pady=10)

# Buton Hover Efektleri (Mikro Animasyon)
def on_enter(e):
    if download_btn['state'] == 'normal':
        download_btn.config(bg="#89B4FA")

def on_leave(e):
    if download_btn['state'] == 'normal':
        download_btn.config(bg="#7AA2F7")

download_btn.bind("<Enter>", on_enter)
download_btn.bind("<Leave>", on_leave)

# Durum Bilgisi ve Progress Bar
status_label = tk.Label(
    root, 
    text="Hazır", 
    font=("Helvetica", 11, "bold"), 
    bg="#1A1B26", 
    fg="#9ECE6A"
)
status_label.pack(pady=5)

# ttk style ayarı (Karanlık tema uyumlu Progressbar)
style = ttk.Style()
style.theme_use('clam')
style.configure(
    "Horizontal.TProgressbar", 
    foreground='#7AA2F7', 
    background='#7AA2F7', 
    troughcolor='#24283B', 
    borderwidth=0, 
    thickness=12
)

progress_bar = ttk.Progressbar(
    root, 
    style="Horizontal.TProgressbar", 
    orient="horizontal", 
    length=500, 
    mode="determinate"
)
progress_bar.pack(pady=5)

# Log Konsolu Çerçevesi
log_lf = tk.LabelFrame(
    root, 
    text=" İşlem Detayları ve Log Çıktısı ", 
    font=("Helvetica", 9, "bold"), 
    bg="#1A1B26", 
    fg="#A9B1D6", 
    bd=1, 
    relief="solid"
)
log_lf.pack(fill="both", expand=True, padx=25, pady=(15, 20))

log_text = ScrolledText(
    log_lf, 
    font=("Consolas", 9), 
    bg="#24283B", 
    fg="#C0CAF5", 
    bd=0, 
    highlightthickness=0, 
    state="disabled"
)
log_text.pack(fill="both", expand=True, padx=5, pady=5)

# Uygulamayı başlat
if __name__ == "__main__":
    root.mainloop()
