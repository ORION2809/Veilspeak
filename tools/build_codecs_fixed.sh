#!/bin/sh
# Downloads the 3GPP fixed-point reference codecs and builds them into third_party/bin.
#   AMR-NB  TS 26.073    AMR-WB  TS 26.173    EVS  TS 26.442
# These are the bit-exact definitions that phones and network equipment have to match (the
# floating-point versions built by build_codecs.sh are equivalent in quality but not
# bit-exact). They are used unmodified, as a cross-check that the modem's results do not
# depend on which implementation of a codec carries the call.
set -e
cd "$(dirname "$0")/.."
D=third_party/3gpp
mkdir -p $D/dl third_party/bin
for item in 26.073:26073-j00:amrnb_fx 26.173:26173-j00:amrwb_fx 26.442:26442-j00:evs_fx; do
  spec=${item%%:*}; rest=${item#*:}; zip=${rest%%:*}; name=${rest##*:}
  [ -f $D/dl/$zip.zip ] || curl -sL -A "Mozilla/5.0" -o $D/dl/$zip.zip "https://www.3gpp.org/ftp/Specs/archive/26_series/$spec/$zip.zip"
  rm -rf $D/tmp_$name $D/src_$name; mkdir -p $D/tmp_$name $D/src_$name
  unzip -q -o $D/dl/$zip.zip -d $D/tmp_$name
  unzip -q -o $D/tmp_$name/*.zip -d $D/src_$name
  rm -rf $D/tmp_$name
done
N=$D/src_amrnb_fx/c-code
W=$D/src_amrwb_fx/c-code
E=$D/src_evs_fx/c-code
B=third_party/bin
F="-O2 -w -fwrapv -DWMOPS=0 -D__MSDOS__"
NB=""
for f in agc autocorr az_lsp bits2prm cl_ltp convolve c1035pf d_plsf d_plsf_5 d_gain_c d_gain_p dec_lag6 d1035pf cor_h \
    enc_lag3 enc_lag6 g_code g_pitch int_lpc inter_36 inv_sqrt lag_wind levinson lsp_az lsp_lsf ol_ltp pitch_fr pitch_ol \
    pow2 pre_big pre_proc pred_lt preemph prm2bits pstfilt q_gain_c q_gain_p q_plsf q_plsf_5 lsfwt reorder residu lsp lpc \
    ec_gains spreproc syn_filt weight_a qua_gain gc_pred q_plsf_3 post_pro dec_lag3 dec_gain d_plsf_3 d4_17pf c4_17pf \
    d3_14pf c3_14pf d2_11pf c2_11pf d2_9pf c2_9pf cbsearch spstproc gain_q cod_amr dec_amr sp_enc sp_dec ph_disp g_adapt \
    calc_en qgain795 qgain475 sqrt_l set_sign s10_8pf bgnscd gmed_n mac_32 ex_ctrl c_g_aver lsp_avg int_lsf c8_31pf \
    d8_31pf p_ol_wgh ton_stab vad1 dtx_enc dtx_dec a_refl b_cn_cod calc_cor hp_max vadname vad2 r_fft lflg_upd e_homing \
    d_homing basicop2 count oper_32b copy log2 set_zero strfunc n_proc sid_sync; do NB="$NB $N/$f.c"; done
WB=""
for f in agc2 autocorr az_isp bits c2t64fx c4t64fx cod_main convolve cor_h_x d2t64fx d4t64fx decim54 dec_main deemph dtx \
    d_gain2 gpclip g_pitch hp6k hp7k hp50 hp400 hp_wsp int_lpc isfextrp isp_az isp_isf lagconc lag_wind levinson lp_dec2 \
    math_op ph_disp pitch_f4 pit_shrp pred_lt4 preemph p_med_ol qisf_ns qpisf_2s q_gain2 q_pulse random residu scale \
    syn_filt updt_tar util voicefac wb_vad weight_a basicop2 count log2 oper_32b homing; do WB="$WB $W/$f.c"; done
clang $F -DMMS_IO -DVAD1 -I$N $N/coder.c $NB -lm -o $B/amrnb_fx_enc_vad1
clang $F -DMMS_IO -DVAD2 -I$N $N/coder.c $NB -lm -o $B/amrnb_fx_enc_vad2
clang $F -DMMS_IO -DVAD1 -I$N $N/decoder.c $NB -lm -o $B/amrnb_fx_dec
clang $F -I$W $W/coder.c $WB -lm -o $B/amrwb_fx_enc
clang $F -I$W $W/decoder.c $WB -lm -o $B/amrwb_fx_dec
I="-I$E/lib_com -I$E/lib_enc -I$E/lib_dec -I$E/basic_op -I$E/basic_math"
clang -O2 -w -fwrapv $I $E/lib_enc/*.c $E/lib_com/*.c $E/basic_op/*.c $E/basic_math/*.c -lm -o $B/EVS_fx_cod
clang -O2 -w -fwrapv $I $E/lib_dec/*.c $E/lib_com/*.c $E/basic_op/*.c $E/basic_math/*.c -lm -o $B/EVS_fx_dec
ls -la $B
