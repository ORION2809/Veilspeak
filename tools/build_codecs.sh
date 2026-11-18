#!/bin/sh
# Downloads the 3GPP reference speech codecs and builds them into third_party/bin.
#   AMR-NB  TS 26.104    AMR-WB  TS 26.204    EVS  TS 26.443
# The 3GPP reference code is published for implementing and testing the standards; it is
# used here unmodified, only to simulate phone calls. It is not part of the product.
set -e
cd "$(dirname "$0")/.."
D=third_party/3gpp
mkdir -p $D/dl third_party/bin
for item in 26.104:26104-j10:amrnb 26.204:26204-j00:amrwb 26.443:26443-j00:evs; do
  spec=${item%%:*}; rest=${item#*:}; zip=${rest%%:*}; name=${rest##*:}
  [ -f $D/dl/$zip.zip ] || curl -sL -A "Mozilla/5.0" -o $D/dl/$zip.zip "https://www.3gpp.org/ftp/Specs/archive/26_series/$spec/$zip.zip"
  rm -rf $D/tmp_$name $D/src_$name; mkdir -p $D/tmp_$name $D/src_$name
  unzip -q -o $D/dl/$zip.zip -d $D/tmp_$name
  unzip -q -o $D/tmp_$name/*.zip -d $D/src_$name
  rm -rf $D/tmp_$name
done
N=$D/src_amrnb/c-code
W=$(ls -d $D/src_amrwb/*/c-code)
E=$D/src_evs/c-code
B=third_party/bin
clang -O2 -w -DVAD1 -I$N $N/encoder.c $N/sp_enc.c $N/interf_enc.c -lm -o $B/amrnb_enc_vad1
clang -O2 -w -DVAD2 -I$N $N/encoder.c $N/sp_enc.c $N/interf_enc.c -lm -o $B/amrnb_enc_vad2
clang -O2 -w -I$N $N/decoder.c $N/sp_dec.c $N/interf_dec.c -lm -o $B/amrnb_dec
clang -O2 -w -I$W $W/enc_acelp.c $W/enc_dtx.c $W/enc_gain.c $W/enc_if.c $W/enc_lpc.c $W/enc_main.c $W/enc_rom.c $W/enc_util.c $W/encoder.c $W/if_rom.c -lm -o $B/amrwb_enc
clang -O2 -w -include stdlib.h -I$W $W/dec_acelp.c $W/dec_dtx.c $W/dec_gain.c $W/dec_if.c $W/dec_lpc.c $W/dec_main.c $W/dec_rom.c $W/dec_util.c $W/decoder.c $W/if_rom.c -lm -o $B/amrwb_dec
clang -O2 -w -I$E/lib_com -I$E/lib_enc -I$E/lib_dec $E/lib_com/*.c $E/lib_enc/*.c -lm -o $B/EVS_cod
clang -O2 -w -I$E/lib_com -I$E/lib_enc -I$E/lib_dec $E/lib_com/*.c $E/lib_dec/*.c -lm -o $B/EVS_dec
ls -la $B
