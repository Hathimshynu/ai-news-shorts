import React from 'react';
import {Composition} from 'remotion';
import {NewsShort, NewsShortProps} from './NewsShort';

const defaultProps: NewsShortProps = {
  durationInFrames: 300,
  hook: 'This changes everything',
  brand: '@ainewsshorts',
  voice: 'job/voice.mp3',
  music: null,
  scenes: [],
  words: [],
};

export const RemotionRoot: React.FC = () => (
  <Composition
    id="NewsShort"
    component={NewsShort}
    width={1080}
    height={1920}
    fps={30}
    durationInFrames={300}
    defaultProps={defaultProps}
    calculateMetadata={({props}) => ({durationInFrames: props.durationInFrames})}
  />
);
