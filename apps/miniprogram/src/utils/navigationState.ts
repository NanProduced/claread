let _navigatingToOnboarding = false

export function setNavigatingToOnboarding(val: boolean) {
  _navigatingToOnboarding = val
}

export function isNavigatingToOnboarding(): boolean {
  return _navigatingToOnboarding
}
