import React from 'react';
import {
  AbsoluteFill,
  Audio,
  Loop,
  OffthreadVideo,
  Sequence,
  interpolate,
  spring,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from 'remotion';

export type Scene = {
  file: string; // path inside public/, e.g. job/clips/clip0.mp4
  text: string; // short on-screen label
  startFrame: number;
  durationInFrames: number;
  clipFrames: number; // length of the stock clip, used to loop short clips
};

export type Word = {word: string; start: number; end: number};

export type NewsShortProps = {
  durationInFrames: number;
  hook: string;
  brand: string;
  voice: string;
  music: string | null;
  scenes: Scene[];
  words: Word[];
};

const FONT = '"Noto Sans", "DejaVu Sans", Arial, sans-serif';
const ACCENT = '#ffd60a';

const Background: React.FC<{scene: Scene; first?: boolean}> = ({scene, first}) => {
  const frame = useCurrentFrame();
  const scale = interpolate(frame, [0, scene.durationInFrames], [1.08, 1.18]);
  const opacity = first ? 1 : interpolate(frame, [0, 6], [0.5, 1], {extrapolateRight: 'clamp'});
  const video = (
    <OffthreadVideo
      src={staticFile(scene.file)}
      muted
      style={{width: '100%', height: '100%', objectFit: 'cover', transform: `scale(${scale})`}}
    />
  );
  const lift = first ? interpolate(frame, [0, 90], [1.25, 1.05], {extrapolateRight: 'clamp'}) : 1.05;
  return (
    <AbsoluteFill style={{opacity, backgroundColor: '#000', filter: `brightness(${lift})`}}>
      {scene.clipFrames > 0 && scene.clipFrames < scene.durationInFrames ? (
        <Loop durationInFrames={scene.clipFrames}>{video}</Loop>
      ) : (
        video
      )}
      <AbsoluteFill
        style={{background: 'linear-gradient(180deg, rgba(0,0,0,.45) 0%, rgba(0,0,0,.15) 40%, rgba(0,0,0,.65) 100%)'}}
      />
    </AbsoluteFill>
  );
};

const SceneLabel: React.FC<{text: string}> = ({text}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const pop = spring({frame, fps, config: {damping: 14}});
  return (
    <div
      style={{
        position: 'absolute',
        top: 300,
        left: 60,
        right: 60,
        display: 'flex',
        justifyContent: 'center',
        transform: `scale(${pop})`,
      }}
    >
      <div
        style={{
          background: 'rgba(255,59,48,.92)',
          color: '#fff',
          fontFamily: FONT,
          fontWeight: 900,
          fontSize: 58,
          padding: '14px 30px',
          borderRadius: 18,
          textTransform: 'uppercase',
          textAlign: 'center',
        }}
      >
        {text}
      </div>
    </div>
  );
};

const Hook: React.FC<{text: string}> = ({text}) => {
  // Frame 0 is the cover on Instagram/Facebook: a bright yellow card with bold black text,
  // so the first frame is never dark and the hook reads instantly in the feed.
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const s = interpolate(spring({frame, fps, config: {damping: 12}}), [0, 1], [0.94, 1]); // never invisible
  const out = interpolate(frame, [70, 80], [1, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  return (
    <AbsoluteFill style={{justifyContent: 'center', alignItems: 'center', padding: 60, opacity: out}}>
      <div
        style={{
          background: ACCENT,
          color: '#0b0f1a',
          fontFamily: FONT,
          fontWeight: 900,
          fontSize: 96,
          lineHeight: 1.08,
          textAlign: 'center',
          textTransform: 'uppercase',
          padding: '56px 48px',
          borderRadius: 36,
          transform: `scale(${s}) rotate(-1.5deg)`,
          boxShadow: '0 24px 60px rgba(0,0,0,.45)',
        }}
      >
        {text}
      </div>
    </AbsoluteFill>
  );
};

const Captions: React.FC<{words: Word[]; hideBefore: number}> = ({words, hideBefore}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  if (frame < hideBefore) return null; // the hook text is on screen
  const t = frame / fps;
  // Group into chunks of 3 words, show the chunk containing the current time.
  const chunks: Word[][] = [];
  for (let i = 0; i < words.length; i += 3) chunks.push(words.slice(i, i + 3));
  const chunk = chunks.find((c) => t >= c[0].start - 0.05 && t <= c[c.length - 1].end + 0.15);
  if (!chunk) return null;
  return (
    <div
      style={{
        position: 'absolute',
        bottom: 520,
        left: 50,
        right: 50,
        display: 'flex',
        flexWrap: 'wrap',
        justifyContent: 'center',
        columnGap: 28,
        fontFamily: FONT,
        fontWeight: 900,
        fontSize: 92,
        lineHeight: 1.15,
        textTransform: 'uppercase',
      }}
    >
      {chunk.map((w, i) => {
        const active = t >= w.start && t <= w.end + 0.05;
        return (
          <span
            key={i}
            style={{
              color: active ? ACCENT : '#fff',
              display: 'inline-block',
              transform: active ? 'scale(1.1)' : 'scale(1)',
              WebkitTextStroke: '4px #000',
              paintOrder: 'stroke fill',
              textShadow: '0 6px 18px rgba(0,0,0,.7)',
            }}
          >
            {w.word.replace(/[.,!?;:]+$/, '')}
          </span>
        );
      })}
    </div>
  );
};

const Chrome: React.FC<{brand: string; total: number}> = ({brand, total}) => {
  const frame = useCurrentFrame();
  return (
    <>
      {brand ? <div
        style={{
          position: 'absolute',
          top: 120,
          left: 60,
          color: '#fff',
          fontFamily: FONT,
          fontWeight: 800,
          fontSize: 40,
          background: 'rgba(0,0,0,.45)',
          padding: '10px 22px',
          borderRadius: 40,
        }}
      >
        {brand}
      </div> : null}
      <div style={{position: 'absolute', bottom: 0, left: 0, height: 12, width: `${(frame / total) * 100}%`, background: ACCENT}} />
    </>
  );
};

export const NewsShort: React.FC<NewsShortProps> = ({durationInFrames, hook, brand, voice, music, scenes, words}) => {
  return (
    <AbsoluteFill style={{backgroundColor: '#0b0f1a'}}>
      {scenes.map((s, i) => (
        <Sequence key={i} from={s.startFrame} durationInFrames={s.durationInFrames}>
          <Background scene={s} first={i === 0} />
          {i > 0 ? <SceneLabel text={s.text} /> : null}
        </Sequence>
      ))}
      <Sequence durationInFrames={80}>
        <Hook text={hook} />
      </Sequence>
      <Captions words={words} hideBefore={75} />
      <Chrome brand={brand} total={durationInFrames} />
      <Audio src={staticFile(voice)} />
      {music ? <Audio src={staticFile(music)} volume={0.08} /> : null}
    </AbsoluteFill>
  );
};
