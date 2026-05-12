const path = require('path')
const config = {
  projectName: 'claread-miniprogram',
  date: '2026-3-29',
  designWidth: 750,
  deviceRatio: {
    640: 2.34 / 2,
    750: 1,
    828: 1.81 / 2
  },
  sourceRoot: 'src',
  outputRoot: 'dist',
  plugins: [],
  defineConstants: {
    TARO_APP_ENV: JSON.stringify(process.env.TARO_APP_ENV || 'local'),
    TARO_APP_API_BASE_URL: JSON.stringify(process.env.TARO_APP_API_BASE_URL || 'http://localhost:8000'),
  },
  copy: {
    patterns: [
    ],
    options: {
    }
  },
  framework: 'react',
  compiler: 'webpack5',
  cache: {
    enable: false // Webpack5 cache 有 bug，暂时禁用
  },
  mini: {
    webpackChain: (chain, webpack) => {
      chain.resolve.alias
        .set('@/config', path.resolve(__dirname, '../src/config'))
        .set('@/components', path.resolve(__dirname, '../src/components'))
        .set('@/utils', path.resolve(__dirname, '../src/utils'))
        .set('@/services', path.resolve(__dirname, '../src/services'))
        .set('@/stores', path.resolve(__dirname, '../src/stores'))
        .set('@/types', path.resolve(__dirname, '../src/types'))
    },
    postcss: {
      pxtransform: {
        enable: true,
        config: {

        }
      },
      url: {
        enable: true,
        config: {
          limit: 1024 // 设定转换尺寸上限
        }
      },
      cssModules: {
        enable: false, // 默认为 false，如需使用 css modules，配置为 true
        config: {
          namingPattern: 'module', // 转换模式，取值为 global/module
          generateScopedName: '[name]__[local]___[hash:base64:5]'
        }
      }
    }
  },
  h5: {
    publicPath: '/',
    staticDirectory: 'static',
    resolve: {
      alias: {
        '@/config': path.resolve(__dirname, '../src/config'),
        '@/components': path.resolve(__dirname, '../src/components'),
        '@/utils': path.resolve(__dirname, '../src/utils'),
        '@/services': path.resolve(__dirname, '../src/services'),
        '@/stores': path.resolve(__dirname, '../src/stores'),
        '@/types': path.resolve(__dirname, '../src/types'),
      },
    },
    postcss: {
      autoprefixer: {
        enable: true,
        config: {
        }
      },
      cssModules: {
        enable: false, // 默认为 false，如需使用 css modules，配置为 true
        config: {
          namingPattern: 'module', // 转换模式，取值为 global/module
          generateScopedName: '[name]__[local]___[hash:base64:5]'
        }
      }
    }
  }
}

module.exports = function (merge) {
  if (process.env.NODE_ENV === 'development') {
    return merge({}, config, require('./dev'))
  }
  return merge({}, config, require('./prod'))
}
