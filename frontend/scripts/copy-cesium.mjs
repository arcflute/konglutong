import {cp,mkdir} from 'node:fs/promises';
await mkdir('dist/cesium',{recursive:true});
for(const part of ['Assets','Widgets','ThirdParty','Workers'])await cp(`node_modules/cesium/Build/Cesium/${part}`,`dist/cesium/${part}`,{recursive:true});
