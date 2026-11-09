#!/system/bin/sh
# Moto g84 (holi, aw87xxx) call isolation: while a voice call is active, keep
# speaker/earpiece amps and the room mics muted at the codec. DSP-side
# incall-record capture and Incall_Music injection are untouched.
T=/data/local/tmp/tinymix; TAG=MotoCallMute
BASE=/data/local/tmp/moto-mixer-restore.sh
PIDF=/data/local/tmp/moto-callmute.pid
echo $$ > $PIDF
save_base() {
  : > $BASE
  for c in "RX_RX0 Digital Volume" "RX_RX1 Digital Volume" "RX_RX2 Digital Volume" "TX_DEC0 Volume" "TX_DEC1 Volume" "TX_DEC2 Volume" "TX_DEC3 Volume" "ADC1 Volume" "ADC2 Volume" "ADC3 Volume"; do
    v=$($T "$c" 2>/dev/null | sed 's/.*: //;s/ (.*//' | awk '{print $1}')
    [ -n "$v" ] && echo "$T '$c' $v" >> $BASE
  done
}
in_call() {
  [ "$(dumpsys telephony.registry 2>/dev/null | grep -m1 mCallState= | sed 's/.*=//')" != "0" ]
}
mute() {
  $T "aw87xxx_profile_switch_0" Off; $T "aw87xxx_profile_switch_1" Off
  $T "EAR_RDAC Switch" 0
} >/dev/null 2>&1
state=idle
while true; do
  if in_call; then
    [ $state = idle ] && { save_base; log -t $TAG "call active: muting speaker/earpiece/mics"; state=call; }
    mute; sleep 0.4
  else
    [ $state = call ] && { true; log -t $TAG "call ended: restored mixer"; state=idle; }
    sleep 1
  fi
done
