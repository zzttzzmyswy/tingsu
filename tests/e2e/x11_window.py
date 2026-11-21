"""Window isolation and refresh on a private X server with a window manager."""
import json
import subprocess
import sys

from PIL import Image
from desktop import wait


def verify_window(page, data, directory):
    color_file = directory / 'slide-color'
    color_file.write_text('#db527e')
    # The test display contains only the application and this synthetic meeting.
    window = subprocess.Popen([sys.executable, '-c', """
import sys,tkinter as tk
from pathlib import Path
root=tk.Tk()
root.title('Tingsu synthetic slide')
root.geometry('400x240+45+45')
def paint():
    root.configure(bg=Path(sys.argv[1]).read_text().strip())
    root.after(120,paint)
paint()
root.mainloop()
""", str(color_file)])
    def window_id():
        found=subprocess.run(['xdotool','search','--onlyvisible','--name','^Tingsu synthetic slide$'],
                             capture_output=True,text=True)
        return found.stdout.strip().splitlines()[0] if found.stdout.strip() else None

    def frames():
        return sorted((data/'tingsu'/'frames').rglob('*.jpg'),key=lambda p:p.stat().st_mtime_ns)
    try:
        target=wait(window_id,'synthetic X11 window')
        page.click('screen')
        page.click('screen-on')
        wait(lambda:subprocess.run(['pgrep','-u',str(__import__('os').getuid()),'-x','xwininfo'],
                                   capture_output=True).returncode==0,'X11 picker')
        subprocess.run(['xdotool','windowactivate','--sync',target,'mousemove','--window',target,'90','90','click','1'],check=True)
        def color(index):
            candidates=frames()
            if not candidates:return False
            with Image.open(candidates[-1]) as image:
                if image.size!=(400,240):return False
                rgb=image.getpixel((100,100))
                return rgb[0]>175 and rgb[1]<110 if index==0 else rgb[1]>120 and rgb[0]<75
        wait(lambda:color(0),'selected red window')
        color_file.write_text('#208d4e')
        import time
        time.sleep(.35)
        page.click('snapshot')
        wait(lambda:color(1),'refreshed green window')
        page.click('screen-off')
        page.click('close',area='#dialog ')
    finally:
        window.terminate()
        window.wait(timeout=5)
