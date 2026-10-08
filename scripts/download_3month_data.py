"""
Automated 3-Month Data Downloader script for 15 target coins (11 Mid-Caps + 4 Large-Caps).
Uses multi-threaded streaming from Binance Data Vision.
"""
import os
import sys
import subprocess

TARGET_SYMBOLS = [
    # 4 Selected Large-Caps
    "NEARUSDT",
    "ICPUSDT",
    "HBARUSDT",
    "FILUSDT",
    # 11 Mid-Caps
    "UNIUSDT",
    "AAVEUSDT",
    "CRVUSDT",
    "PENDLEUSDT",
    "CAKEUSDT",
    "ONDOUSDT",
    "ENAUSDT",
    "ARBUSDT",
    "POLUSDT",
    "SEIUSDT",
    "CFXUSDT",
]


def download_all():
    print("================================================================================")
    print("🚀 LAUNCHING 3-MONTH HISTORICAL 1M DATA DOWNLOAD FOR 15 TARGET COINS")
    print("================================================================================")
    print(f"Target Symbols ({len(TARGET_SYMBOLS)}): {', '.join(TARGET_SYMBOLS)}")
    
    script_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "binance_downloader.py")
    output_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data_store")
    
    cmd = [
        sys.executable,
        script_path,
        "--symbols",
    ] + TARGET_SYMBOLS + [
        "--latest-complete-3-months",
        "--output-dir", output_dir
    ]
    
    print(f"\nExecuting: {' '.join(cmd)}\n")
    res = subprocess.run(cmd)
    if res.returncode == 0:
        print("\n✅ All 3-month historical data successfully downloaded into data_store/")
    else:
        print(f"\n❌ Download process returned exit code {res.returncode}")
        sys.exit(res.returncode)


if __name__ == "__main__":
    download_all()
