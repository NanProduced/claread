export default {
  pages: [
    'pages/home/index',
    'pages/input/index',
    'pages/result/index',
  ],
  subPackages: [
    {
      root: 'packageA',
      pages: [
        'history/index',
        'vocab/index',
        'vocab-review/index',
        'profile/index',
        'credit-detail/index',
      ],
    },
    {
      root: 'packageB',
      pages: [
        'daily-reader/index',
        'daily-reader-archive/index',
      ],
    },
    {
      root: 'packageC',
      pages: [
        'feedback/index',
        'feedback/my-feedback',
        'onboarding/index',
        'about/index',
        'agreement/index',
      ],
    },
  ],
  window: {
    backgroundTextStyle: 'light',
    navigationBarBackgroundColor: '#FAF9F6',
    navigationBarTitleText: 'Claread透读',
    navigationBarTextStyle: 'black',
    navigationStyle: 'custom',
  },
}
