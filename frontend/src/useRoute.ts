import { useCallback, useEffect, useState } from 'react'

export type Route = '/join' | '/session' | '/privacy' | 'not-found'
function readRoute(): Route {
  const path = window.location.pathname.replace(/\/$/, '') || '/'
  if (path === '/') return '/join'
  return path === '/join' || path === '/session' || path === '/privacy' ? path : 'not-found'
}

/** Only non-sensitive paths enter browser history. Session state stays in React. */
export function useRoute() {
  const [route, setRoute] = useState<Route>(readRoute)
  const navigate = useCallback((path: Exclude<Route, 'not-found'>, replace = false) => {
    window.history[replace ? 'replaceState' : 'pushState'](null, '', path)
    setRoute(readRoute())
  }, [])

  useEffect(() => {
    if (window.location.pathname === '/') navigate('/join', true)
    const onPopState = () => setRoute(readRoute())
    window.addEventListener('popstate', onPopState)
    return () => window.removeEventListener('popstate', onPopState)
  }, [navigate])

  return { route, navigate }
}
