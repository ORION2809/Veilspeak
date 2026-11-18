1;
% E5: multi-tone 4-PSK through codecs: SNR vs codec bitrate (cf. P2 Fig. 3) and distortion statistics (cf. Figs. 5-7).
cd('/home/mivi/dov/DoV_technique/simulation_scripts');
addpath('../channel'); pkg load signal; warning('off','all');
f = 400:400:3200; H = numel(f); Ls = 40; Ns = 5000; rand('seed',5);
ph = randi(4,H,Ns)*pi/2; t = 0:Ls*Ns-1; s = zeros(1,Ls*Ns);
for i=1:H, p = repmat(ph(i,:),Ls,1)(:)'; s = s + sin(2*pi*f(i)*t/8000 + p); end
s = s/H*0.8;
function y = amr_mode(x, mode)   % same as authors' AMR_channel but with selectable mode
  fid=fopen('../channel/temp/e5_in.raw','w'); fwrite(fid,x*2^15,'int16'); fclose(fid);
  system(['../channel/codecs/encoder_2018 ' mode ' ../channel/temp/e5_in.raw ../channel/temp/e5.amr > /dev/null 2>&1']);
  system('../channel/codecs/decoder_2018 ../channel/temp/e5.amr ../channel/temp/e5_out.raw > /dev/null 2>&1');
  fid=fopen('../channel/temp/e5_out.raw','r'); y=fread(fid,'int16'); fclose(fid);
  y=[y(41:end); zeros(160,1)]/2^15; y=y';
end
snr = @(x,y) 10*log10(sum(x.^2)/sum((y(1:numel(x))-x).^2));
fid = fopen('/home/mivi/dov/E5_snr.csv','w'); fprintf(fid,'codec,bitrate_bps,SNR_dB\n');
amr = {'MR475',4750;'MR515',5150;'MR59',5900;'MR67',6700;'MR74',7400;'MR795',7950;'MR102',10200;'MR122',12200};
for i=1:rows(amr), fprintf(fid,'AMR,%d,%.2f\n',amr{i,2},snr(s,amr_mode(s,amr{i,1}))); fflush(fid); end
for r=[8000 11000 15000], fprintf(fid,'SPEEX,%d,%.2f\n',r,snr(s,SPEEX_channel(s,r)')); fflush(fid); end
for r=7000:2000:15000, fprintf(fid,'SILK,%d,%.2f\n',r,snr(s,OPUS_SILK_channel(s,r)')); fflush(fid); end
fclose(fid);
% distortion statistics at the paper's fixed rates
fid = fopen('/home/mivi/dov/E5_distortion.csv','w');
fprintf(fid,'codec,harmonic_Hz,mean_phase_shift_rad,residual_var_norm,mardia_skew,mardia_kurt(normal=8),corr_time_lag1,corr_next_harmonic\n');
chans = {'AMR12.2',amr_mode(s,'MR122'); 'SPEEX11',SPEEX_channel(s,11000)'; 'SILK12',OPUS_SILK_channel(s,12000)'};
for c=1:rows(chans)
  y = chans{c,2}(1:numel(s)); S = fft(reshape(s,Ls,Ns)); Y = fft(reshape(y,Ls,Ns)); bins = f*Ls/8000+1;
  R = zeros(H,Ns);
  for k=1:H, R(k,:) = Y(bins(k),:)./S(bins(k),:); end            % complex gain per symbol (1 = no distortion)
  for k=1:H
    sh = angle(mean(R(k,:))); r = R(k,:)*exp(-1j*sh) - abs(mean(R(k,:)));   % residual after constant phase shift
    X = [real(r)' imag(r)']; X = X - mean(X); Sinv = inv(cov(X));
    d = sum((X*Sinv).*X,2); kurt = mean(d.^2);
    idx = 1:min(1500,Ns); G = X(idx,:)*Sinv*X(idx,:)'; skew = mean(G(:).^3);
    ct = abs(corr(r(1:end-1).', r(2:end).'));
    if k<H, r2 = R(k+1,:)*exp(-1j*angle(mean(R(k+1,:)))); ch = abs(corr(r.', (r2-mean(r2)).')); else ch = NaN; end
    fprintf(fid,'%s,%d,%.3f,%.4f,%.3f,%.2f,%.3f,%.3f\n',chans{c,1},f(k),sh,var(r)/abs(mean(R(k,:)))^2,skew,kurt,ct,ch);
  end
  fflush(fid);
end
fclose(fid); system('touch /home/mivi/dov/E5.done');
