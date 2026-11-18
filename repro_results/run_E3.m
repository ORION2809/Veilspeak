1;
% E3: full DoV frames with RS(GF64) FEC, 3G (low) and VoIP (high) parameter sets. Calls the authors' functions unmodified.
cd('/home/mivi/dov/DoV_technique/simulation_scripts');
addpath('../analysis_synthesis'); addpath('../channel'); addpath('../RS_coding'); addpath('../RS_coding/RS_functions');
pkg load signal; pkg load communications;
warning('off','all');
cases = {'parameters_RS_real_3G','AMR',12200,'correction_10_AMR_40';
         'parameters_RS_real_3G','SILK',12000,'correction_10_OPUS_40';
         'parameters_RS_real_VoIP','AMR',12200,'correction_8_AMR_20';
         'parameters_RS_real_VoIP','SILK',12000,'correction_8_OPUS_20';
         'parameters_RS_real_VoIP','SILK',16000,'correction_8_OPUS_20';
         'parameters_RS_real_VoIP','SILK',24000,'correction_8_OPUS_20'};
fid = fopen('/home/mivi/dov/E3_fec.csv','w');
fprintf(fid,'params,codec,rate,erasures,codebooks_K,codebook_L,frames,SER,FER,BER,gross_kbps,payload_kbps\n');
for i = 1:rows(cases)
  [rs, ss] = feval(cases{i,1});
  [K,L,M] = size(ss.codebooks_Tx);
  c = load(['../channel_corrections_examples/' cases{i,4} '.mat']); cr = c.correction;
  cr.phase_shift = repmat(reshape(cr.phase_shift,1,[]),K,1); cr.mag_var = repmat(reshape(cr.mag_var,1,[]),K,1);
  nframes = 300; rand('seed',9);
  data_bits = randi(2,1,nframes*rs.message_length)-1; id_codebook = floor(rand(1,nframes+10)*K+1);
  [signal, id_sym] = encode_and_synthesize_DoV_signal(data_bits, id_codebook, ss, rs, 0);
  signal = signal./max(signal)/2;
  switch cases{i,2}
    case 'AMR',  rx = AMR_channel(signal)';
    case 'SILK', rx = OPUS_SILK_channel(signal,cases{i,3})';
  end
  frame_s = (ss.block_duration + ss.training_sequence) * M / 8000;   % block = data symbols + header
  for er = [0 1]
    [dec, id_rec, cb_rec, success] = demodulate_and_decode_DoV_signal_RS_code(rx, ss, rs, er, cr);
    Ks = min(numel(id_sym),numel(id_rec)); Kb = min(numel(data_bits),numel(dec));
    fprintf(fid,'%s,%s,%d,%d,%d,%d,%d,%.6g,%.6g,%.6g,%.2f,%.2f\n',cases{i,1},cases{i,2},cases{i,3},er,K,L,numel(success), ...
      mean(id_sym(1:Ks)!=id_rec(1:Ks)), 1-mean(success), mean(data_bits(1:Kb)!=dec(1:Kb)), ...
      log2(L)*(ss.block_duration+ss.training_sequence)/frame_s/1000, rs.message_length/frame_s/1000); fflush(fid);
  end
end
fclose(fid); system('touch /home/mivi/dov/E3.done');
