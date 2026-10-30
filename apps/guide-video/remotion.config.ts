import {Config} from '@remotion/cli/config';

Config.setVideoImageFormat('jpeg');
Config.setPixelFormat('yuv420p');
Config.setCodec('h264');
Config.setCrf(20);
// ponytail: 4 workers keeps peak RSS sane on this laptop; raise if renders are slow on a bigger box.
Config.setConcurrency(4);
