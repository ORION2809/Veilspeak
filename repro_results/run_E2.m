1;
% E2: SER vs codebook size at 200 baud (40-sample symbols), cf. P2 Fig. 11a/b. Calls the authors' functions unmodified.
cd('/home/mivi/dov/DoV_technique/simulation_scripts');
addpath('../analysis_synthesis'); addpath('../channel');
pkg load signal; pkg load communications;
warning('off','all');
harm = str2num(getenv('HARM')); sym_num = 20000;
fid = fopen(sprintf('/home/mivi/dov/E2_h%d.csv',harm),'w');
fprintf(fid,'harmonics,codebook,bits,bitrate_kbps,codebook_file,codec,rate,SER_corrected,SER_uncorrected,symbols\n');
for L = 2.^(8:16)
  name = sprintf('../codebooks_examples/codebooks_%d_%d_40_new.mat',harm,L);
  if !exist(name,'file'), name = sprintf('../codebooks_examples/codebooks_%d_%d_40.mat',harm,L); end
  if !exist(name,'file'), fprintf(fid,'%d,%d,,,MISSING,,,,,\n',harm,L); continue; end
  load(name);
  ss = struct('codebooks_Tx',codebooks_Tx,'codebooks_Rx',codebooks_Rx,'block_duration',1e7,'silence_duration',0,'training_sequence',0);
  rand('seed',6); id_sym = randi(L,1,sym_num);
  signal = synthesize_DoV_signal(id_sym, ones(1,10), ss); signal = signal./max(signal)/2;
  runs = {'AMR',12200,'AMR'; 'SPEEX',11000,'SPEEX'; 'SILK',12000,'OPUS'};
  if harm == 8, runs(end+1,:) = {'SILK',14000,'OPUS'}; runs(end+1,:) = {'SILK',16000,'OPUS'}; end
  for r = 1:rows(runs)
    codec = runs{r,1}; rate = runs{r,2};
    switch codec
      case 'AMR',   rx = AMR_channel(signal)';
      case 'SPEEX', rx = SPEEX_channel(signal,rate)';
      case 'SILK',  rx = OPUS_SILK_channel(signal,rate)';
    end
    c = load(sprintf('../channel_corrections_examples/correction_%d_%s_40.mat',harm,runs{r,3}));
    nc = c.correction; nc.phase_shift = zeros(size(nc.phase_shift)); nc.mag_var = ones(size(nc.mag_var));
    a = demodulate_DoV_signal_with_correction(rx, ss, c.correction);
    b = demodulate_DoV_signal_with_correction(rx, ss, nc);
    K = min(numel(id_sym),numel(a));
    fprintf(fid,'%d,%d,%d,%.1f,%s,%s,%d,%.6g,%.6g,%d\n',harm,L,log2(L),log2(L)*0.2,name(22:end),codec,rate, ...
            mean(id_sym(1:K)!=a(1:K)), mean(id_sym(1:K)!=b(1:K)), K); fflush(fid);
  end
end
fclose(fid); system(sprintf('touch /home/mivi/dov/E2_h%d.done',harm));
