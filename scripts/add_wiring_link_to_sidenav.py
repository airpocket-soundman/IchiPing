"""Add 配線図 link to 設計・仕様 sidenav group across all HTML pages.

Strategy: locate the '<div class="sidenav-h">設計 / 仕様</div>' marker,
then insert a new <a href="{rel}/hardware/wiring.html">配線図</a> line right after
the 仕様書 link, with the correct relative path based on each file's location.
"""
from __future__ import annotations

import os
import re
from pathlib import Path


def rel_path_to_hardware(html_file: Path, repo_root: Path) -> str:
    """Return relative path string from html_file's dir to hardware/wiring.html."""
    target = repo_root / 'hardware' / 'wiring.html'
    rel = os.path.relpath(target, html_file.parent).replace('\\', '/')
    return rel


def process(html_file: Path, repo_root: Path) -> tuple[bool, str]:
    text = html_file.read_text(encoding='utf-8')

    # If 配線図 link already present, skip.
    if '">配線図</a>' in text:
        return False, 'already has 配線図 link'

    # Find the 設計 / 仕様 group
    marker = '<div class="sidenav-h">設計 / 仕様</div>'
    if marker not in text:
        return False, 'no 設計/仕様 marker'

    rel = rel_path_to_hardware(html_file, repo_root)

    # Insert new link right after the 仕様書 link.
    # The existing 仕様書 link looks like: <a href="..../spec.html">仕様書 (spec)</a>
    # or sometimes class="current".
    spec_pattern = re.compile(
        r'(<a href="[^"]*spec\.html"(?: class="current")?>仕様書 \(spec\)</a>)'
    )
    m = spec_pattern.search(text)
    if not m:
        return False, 'no 仕様書 link found in expected pattern'

    inserted_link = f'\n      <a href="{rel}">配線図</a>'
    new_text = text[:m.end()] + inserted_link + text[m.end():]
    html_file.write_text(new_text, encoding='utf-8')
    return True, f'inserted with rel={rel}'


def main():
    repo_root = Path('.').resolve()

    files = [
        'hardware/wiring.html', 'hardware/bom.html', 'hardware/pin_plan.html',
        'hardware/display_options.html',
        'docs/home.html', 'docs/nn_review.html', 'docs/mcu_deployment.html',
        'docs/near_term_plan.html', 'docs/nn_design.html', 'docs/v12345_report.html',
        'docs/nn_train_v1v2v3_combined.html', 'docs/nn_train_v1v2_combined.html',
        'docs/nn_train_v1_result.html', 'docs/nn_train_extra_experiments.html',
        'docs/nn_methods_compare.html', 'docs/full32_passive_test.html',
        'docs/full32_noise_test.html', 'docs/full32_initial_test.html',
        'docs/vscode_setup.html', 'docs/solist_porting.html',
        'docs/servo_coords.html', 'docs/probe_sound.html', 'docs/bringup.html',
        'pc/training/README.html', 'pc/README.html',
        'firmware/README.html', 'firmware/host_build/README.html',
        'firmware/projects/01_dummy_emitter/README.html',
        'firmware/projects/02_servo_test/README.html',
        'firmware/projects/03_ili9341_test/README.html',
        'firmware/projects/04_lvgl_test/README.html',
        'firmware/projects/05_usb_cdc_emitter/README.html',
        'firmware/projects/06_mic_test/README.html',
        'firmware/projects/07_speaker_test/README.html',
        'firmware/projects/08_mic_speaker_test/README.html',
        'firmware/projects/09_collector/README.html',
        'firmware/projects/10_inference/README.html',
        'index.html', 'tasks.html', 'CLAUDE.html',
    ]

    n_done = 0
    n_skip = 0
    for fname in files:
        path = repo_root / fname
        if not path.exists():
            print(f'  MISSING: {fname}')
            continue
        ok, msg = process(path, repo_root)
        if ok:
            n_done += 1
            print(f'  OK: {fname}  ({msg})')
        else:
            n_skip += 1
            print(f'  skip: {fname}  ({msg})')

    print(f'done: {n_done} updated, {n_skip} skipped')


if __name__ == '__main__':
    main()
