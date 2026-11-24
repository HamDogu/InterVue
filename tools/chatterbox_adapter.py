#!/usr/bin/env python3
"""Optional command adapter: run using the existing Chatterbox virtual environment.
Do not install a second copy of Chatterbox; verify against the installed version.
"""
import argparse,os
from pathlib import Path

def main():
    p=argparse.ArgumentParser();p.add_argument('--text',required=True);p.add_argument('--reference',required=True);p.add_argument('--output',required=True);a=p.parse_args()
    import torch,torchaudio
    from chatterbox.tts import ChatterboxTTS
    device=os.getenv('CHATTERBOX_DEVICE','cuda' if torch.cuda.is_available() else 'cpu')
    model=ChatterboxTTS.from_pretrained(device=device)
    audio=model.generate(a.text,audio_prompt_path=a.reference)
    torchaudio.save(a.output,audio.cpu(),model.sr)
if __name__=='__main__':main()
