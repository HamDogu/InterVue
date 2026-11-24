#!/usr/bin/env python3
import importlib.util, shutil, subprocess,sys
print('Python:',sys.version.split()[0])
for name in ['fastapi','uvicorn','faster_whisper','cv2','mediapipe']:
    print(f'{name}:', 'installed' if importlib.util.find_spec(name) else 'MISSING')
for name in ['ffmpeg','ffprobe','node','npm']:
    print(f'{name}:',shutil.which(name) or 'MISSING')
if shutil.which('ffmpeg'):
    output=subprocess.run(['ffmpeg','-hide_banner','-filters'],capture_output=True,text=True)
    print('libvmaf:', 'available' if any(' libvmaf ' in l for l in output.stdout.splitlines()) else 'MISSING — original videos retained')
