/**
 * 环境配置
 *
 * local:  本机后端，单人联调
 * dev:    共享开发环境，真机多人联调
 * prod:   正式环境
 */

export type Env = 'local' | 'dev' | 'staging' | 'prod'

export interface EnvConfig {
  env: Env
  apiBaseUrl: string
}

// 小程序环境无 process 对象，只能使用 Taro defineConstants 编译时注入。
declare const TARO_APP_ENV: string | undefined
declare const TARO_APP_API_BASE_URL: string | undefined

const injectedEnv = typeof TARO_APP_ENV !== 'undefined' ? TARO_APP_ENV : undefined
const injectedApiBaseUrl = typeof TARO_APP_API_BASE_URL !== 'undefined' ? TARO_APP_API_BASE_URL : undefined
const env: Env = (injectedEnv as Env) || 'local'

const defaultApiBaseUrl = injectedApiBaseUrl || 'http://localhost:8000'

const envConfigs: Record<Env, EnvConfig> = {
  local: {
    env: 'local',
    apiBaseUrl: defaultApiBaseUrl,
  },
  dev: {
    env: 'dev',
    apiBaseUrl: defaultApiBaseUrl,
  },
  staging: {
    env: 'staging',
    apiBaseUrl: injectedApiBaseUrl || 'https://staging-api.claread.com',
  },
  prod: {
    env: 'prod',
    apiBaseUrl: injectedApiBaseUrl || 'https://api.claread.com',
  },
}

export const envConfig: EnvConfig = envConfigs[env]
