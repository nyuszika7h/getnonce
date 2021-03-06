#!/usr/bin/env python3

import atexit
import base64
import os
import shutil
import signal
import subprocess
import sys
import time

try:
    from termcolor import colored
except ModuleNotFoundError:
    def colored(text, *args, **kwargs):
        return text

signal.signal(signal.SIGINT, signal.SIG_DFL)


@atexit.register
def finish():
    print('\nPress Enter to exit.')
    input()


def run_process(command, *args, silence_errors=False, timeout=None):
    """Run a command with the specified arguments."""
    try:
        p = subprocess.run(
            [command, *args], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding='utf-8', timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return None

    if p.returncode != 0:
        if silence_errors:
            return None
        else:
            print(colored(p.stdout, 'red'))
            sys.exit(1)

    return p.stdout.strip()


def wait_for_device(mode):
    """Wait for a device to be connected over USB and unlocked."""

    if mode == 'normal':
        print(colored('Waiting for device to be connected and unlocked', 'yellow'), end='', flush=True)

        while not run_process('idevicediagnostics', 'diagnostics', silence_errors=True):
            print(colored('.', 'yellow'), end='', flush=True)
            time.sleep(1)
    elif mode == 'recovery':
        print('Waiting for device', end='', flush=True)

        while not run_process('irecovery', '-m', silence_errors=True):
            print('.', end='', flush=True)

    print()


def mobilegestalt_read_int(key):
    """Read an integer from MobileGestalt and return it as a hex string."""

    value = run_process('ideviceinfo', '-k', key).encode('utf-8')

    if value:
        return '{:X}'.format(int(value))
    else:
        return None


def mobilegestalt_read_bytes(key, endianness):
    """Read bytes with the specified endianness from MobileGestalt and return it as a hex string."""

    value = base64.b64decode(run_process('ideviceinfo', '-k', key).encode('utf-8'))

    if value:
        return '{:x}'.format(int.from_bytes(value, endianness))
    else:
        return None


def pad_apnonce(apnonce):
    """Pad an ApNonce to 64 characters (A10 and above) or 40 characters (A9 and below)."""

    padded = apnonce.zfill(64)

    if padded[0:24] == '000000000000000000000000':
        return padded[24:]
    else:
        return padded


def get_recovery_apnonce(old_apnonce):
    """Read the ApNonce in recovery mode."""

    apnonce = None

    wait_for_device(mode='recovery')

    info = run_process('irecovery', '-q')

    if not info:
        print(colored('ERROR: Unable to read ApNonce', 'red'))
        sys.exit(1)

    for line in info.splitlines():
        key, value = line.split(': ')
        if key == 'NONC':
            apnonce = value
            break

    if apnonce:
        print(colored(f"ApNonce = {colored(apnonce, attrs=['bold'])}", 'green'))
    else:
        print(colored('ERROR: Unable to read ApNonce', 'red'))
        sys.exit(1)

    if old_apnonce and apnonce != old_apnonce:
        print(colored('ERROR: ApNonce does not match', 'red'))

        print('Exiting recovery mode')
        run_process('irecovery', '-n')

        sys.exit(1)

    return apnonce


if __name__ == '__main__':
    os.environ['PATH'] = os.pathsep.join(['.', os.environ['PATH']])
    for binary in ['idevice_id', 'idevicediagnostics', 'ideviceinfo', 'irecovery']:
        if not shutil.which(binary):
            print(colored(f'ERROR: {binary} not found. Please place the binary in your PATH or the same folder as the '
                          f'script and try again.', 'red'))
            sys.exit(1)

    answer = input('Is your device jailbroken? [y/n] ')
    if answer.lower() == 'y':
        jailbroken = True
    elif answer.lower() == 'n':
        jailbroken = False
    else:
        print(colored('ERROR: Invalid input', 'red'))
        sys.exit(1)

    # If the device is in recovery mode, exit it.
    print(colored('\n[1/5] Checking device state', attrs=['bold']))
    if run_process('irecovery', '-m', silence_errors=True, timeout=1) == 'Recovery Mode':
        print('Exiting recovery mode')
        run_process('irecovery', '-n')
    wait_for_device(mode='normal')

    print(colored('\n[2/5] Getting ECID', attrs=['bold']))
    ecid = mobilegestalt_read_int('UniqueChipID')
    print(colored(f"ECID (hex) = {colored(ecid, attrs=['bold'])}", 'green'))

    # If the device is not jailbroken, get the ApNonce in normal mode, which will set a new random generator.
    print(colored('\n[3/5] Getting ApNonce', attrs=['bold']))
    if jailbroken:
        print('Skipping on jailbroken device')
        apnonce = None
    else:
        apnonce = mobilegestalt_read_bytes('ApNonce', 'big')
        if apnonce:
            apnonce = pad_apnonce(apnonce)
            print(colored(f"ApNonce = {colored(apnonce, attrs=['bold'])}", 'green'))
        else:
            print(colored('ERROR: Unable to read ApNonce', 'red'))
            sys.exit(1)

    # Reboot the device to make sure we get an up to date generator value, then read it out.
    print(colored('\n[4/5] Getting generator', attrs=['bold']))
    print('Rebooting device')
    run_process('idevicediagnostics', 'restart')
    wait_for_device(mode='normal')
    generator = mobilegestalt_read_bytes('BootNonce', 'little')
    if generator:
        print(colored(f"Generator = {colored('0x' + generator.zfill(16), attrs=['bold'])}", 'green'))
    else:
        print(colored('ERROR: Unable to read generator', 'red'))
        sys.exit(1)

    # Read the ApNonce in recovery mode to confirm it matches.
    print(colored('\n[5/5] Verifying ApNonce', attrs=['bold']))
    print('Entering recovery mode')
    udid = run_process('idevice_id', '-l')
    if not udid:
        print(colored('ERROR: Unable to find device', 'red'))
        sys.exit(1)
    run_process('ideviceenterrecovery', udid)
    apnonce = get_recovery_apnonce(apnonce)

    # Reset and read it again to make sure the generator was set properly.
    print('Rebooting device')
    run_process('irecovery', '-c', 'reset')
    time.sleep(5)  # A delay is needed here to make sure it doesn't catch the device before it started exiting recovery
    apnonce = get_recovery_apnonce(apnonce)

    # Return to normal mode.
    print('Exiting recovery mode')
    run_process('irecovery', '-n')

    print(colored('\nAll done! You can go to https://tsssaver.1conan.com/v2/ to save blobs.', attrs=['bold']))
    print('Make sure to specify the ECID, ApNonce and Generator values you got above.')
