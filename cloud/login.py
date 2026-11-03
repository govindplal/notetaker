"""Remote one-time sign-in for the recorder Chrome, running in Modal.

Starts a virtual desktop (Xvfb + x11vnc + noVNC) with Chromium on a persistent Volume
("notetaker-profile"). Open the printed URL in your own browser, sign in to your course site,
then stop the run. The cookies live in the Volume, so later capture runs reuse them.

Run:  modal run cloud/login.py --url https://your-course-site.example.com
"""

import secrets
import subprocess
import time

import modal

APP = modal.App("notetaker-login")
PROFILE = modal.Volume.from_name("notetaker-profile", create_if_missing=True)
PROFILE_DIR = "/profile"
NOVNC_PORT = 6080
MINUTES = 20

image = modal.Image.debian_slim(python_version="3.12").apt_install(
    "chromium",
    "xvfb",
    "x11vnc",
    "novnc",
    "websockify",
    "fluxbox",
    "fonts-liberation",
)


@APP.function(image=image, volumes={PROFILE_DIR: PROFILE}, timeout=MINUTES * 60 + 300)
def login_desktop(url: str, minutes: int = MINUTES) -> None:
    procs = [
        subprocess.Popen(["Xvfb", ":99", "-screen", "0", "1280x800x24"]),
    ]
    time.sleep(2)
    import os

    env = {**os.environ, "DISPLAY": ":99"}
    procs.append(subprocess.Popen(["fluxbox"], env=env))
    # Random per-run password (VNC uses only the first 8 characters).
    password = secrets.token_urlsafe(6)[:8]
    procs.append(
        subprocess.Popen(
            ["x11vnc", "-display", ":99", "-forever", "-passwd", password, "-shared", "-quiet"],
            env=env,
        )
    )
    procs.append(
        subprocess.Popen(
            ["websockify", "--web", "/usr/share/novnc", str(NOVNC_PORT), "localhost:5900"]
        )
    )
    procs.append(
        subprocess.Popen(
            [
                "chromium",
                f"--user-data-dir={PROFILE_DIR}/chrome-profile",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--remote-debugging-port=9222",
                "--remote-debugging-address=127.0.0.1",
                "--start-maximized",
                url,
            ],
            env=env,
        )
    )
    with modal.forward(NOVNC_PORT) as tunnel:
        print(
            f"\nOPEN THIS IN YOUR BROWSER (remote desktop):\n{tunnel.url}/vnc.html?autoconnect=1\n"
        )
        print(f"VNC password: {password}", flush=True)
        print(f"Sign-in session stops after {minutes} minutes. Log in before then.\n", flush=True)
        end = time.time() + minutes * 60
        while time.time() < end:
            time.sleep(30)
            PROFILE.commit()  # persist cookies as they change
    PROFILE.commit()
    for p in procs:
        p.terminate()
    print("Session ended; profile committed to Volume notetaker-profile.")


@APP.local_entrypoint()
def main(url: str = "about:blank", minutes: int = MINUTES) -> None:
    login_desktop.remote(url, minutes)
