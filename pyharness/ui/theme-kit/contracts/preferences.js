import {normalizeConfig} from '../tokens/themes.js';
export function preferenceStore(storage,key='pyharness.appearance.v1') {
  return {
    async load() {try {const raw=storage.getItem(key);if(raw)return normalizeConfig(JSON.parse(raw));
      const legacy=storage.getItem('ph-theme');return normalizeConfig(['light','dark'].includes(legacy)?{themeId:legacy}:{});
    } catch{return normalizeConfig();}},
    async save(value){try{storage.setItem(key,JSON.stringify(normalizeConfig(value)));return true;}catch{return false;}},
    async reset(){try{storage.removeItem(key);storage.removeItem('ph-theme');return true;}catch{return false;}},
  };
}
