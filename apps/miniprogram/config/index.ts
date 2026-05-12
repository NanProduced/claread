import { defineConfig, type Config } from '@tarojs/cli'

const appEnv = process.env.TARO_APP_ENV || 'local'
const apiBaseUrl = process.env.TARO_APP_API_BASE_URL || 'http://localhost:8000'

export default defineConfig({
  defineConstants: {
    TARO_APP_ENV: JSON.stringify(appEnv),
    TARO_APP_API_BASE_URL: JSON.stringify(apiBaseUrl),
  },
  mini: {
    webpackChain(chain) {
      chain.plugins.store.forEach((plugin, name) => {
        if (name.includes('mini-css-extract') || name.includes('cssExtract')) {
          chain.plugin(name).tap((args) => {
            if (!args[0]) args[0] = {}
            args[0].ignoreOrder = true
            return args
          })
        }
      })
    },
  },
} satisfies Config)
