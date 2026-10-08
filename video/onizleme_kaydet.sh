#!/bin/bash
# Kullanim: onizleme_kaydet.sh <kaynak_klasor> <hedef_alt_klasor> <mesaj>
# Kucuk onizleme dosyalarini (kare tablolari, raporlar) 'medya-onizleme' dalina yazar.
set -euo pipefail
src=$1; dest=$2; msg=$3
git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
if git fetch -q origin medya-onizleme:medya-onizleme 2>/dev/null; then
  git worktree add -q /tmp/oniz medya-onizleme
else
  git worktree add -q --orphan -b medya-onizleme /tmp/oniz
fi
rm -rf "/tmp/oniz/$dest" && mkdir -p "/tmp/oniz/$dest" && cp -r "$src"/. "/tmp/oniz/$dest/"
git -C /tmp/oniz add -A
git -C /tmp/oniz commit -qm "$msg"
git -C /tmp/oniz push -q origin medya-onizleme
