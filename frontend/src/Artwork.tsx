export function Shield({ className = '' }: { className?: string }) {
  return <svg className={className} width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="m12 3 8 3v5c0 5-4.5 8.5-8 10-3.5-1.5-8-5-8-10V6l8-3Z" stroke="currentColor" strokeWidth="1.6" /><path d="m8.5 11.5 2.5 2.5 4.5-5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" /></svg>
}

export function Robot({ small = false }: { small?: boolean }) {
  return <svg className={small ? 'robot robot-small' : 'robot'} viewBox="0 0 240 210" fill="none" role="img" aria-label="A smiling teal robot waving hello">
    <defs><linearGradient id="shell" x1="80" y1="40" x2="165" y2="160" gradientUnits="userSpaceOnUse"><stop stopColor="#c8fff2"/><stop offset="1" stopColor="#55c9c3"/></linearGradient><linearGradient id="body" x1="90" y1="145" x2="145" y2="200" gradientUnits="userSpaceOnUse"><stop stopColor="#8ce9d8"/><stop offset="1" stopColor="#36a8aa"/></linearGradient></defs>
    <ellipse cx="120" cy="199" rx="61" ry="7" fill="#07151e" opacity=".5"/>
    <path d="M158 164c26 6 36-10 39-31" stroke="#64d5ca" strokeWidth="16" strokeLinecap="round"/>
    <path d="m191 135-3-13m9 11 7-17m-2 20 13-9" stroke="#9aeee0" strokeWidth="7" strokeLinecap="round"/>
    <path d="M78 162c-14 6-20 14-22 26" stroke="#67d8ce" strokeWidth="15" strokeLinecap="round"/>
    <rect x="84" y="145" width="72" height="49" rx="22" fill="url(#body)"/>
    <rect x="100" y="185" width="13" height="13" rx="5" fill="#6bd9ce"/><rect x="129" y="185" width="13" height="13" rx="5" fill="#6bd9ce"/>
    <circle cx="120" cy="168" r="6" fill="#133944"/><circle cx="120" cy="168" r="2.5" fill="#91ffde"/>
    <path d="M120 52V36" stroke="#8de8d7" strokeWidth="5"/><circle cx="120" cy="30" r="7" fill="#9afce2"/>
    <rect x="49" y="87" width="17" height="33" rx="8" fill="#49b4b4"/><rect x="174" y="87" width="17" height="33" rx="8" fill="#49b4b4"/>
    <rect x="60" y="53" width="120" height="99" rx="34" fill="url(#shell)"/>
    <rect x="72" y="70" width="96" height="65" rx="24" fill="#102d39"/>
    <path d="M89 99c0-8 12-8 12 0m38 0c0-8 12-8 12 0" stroke="#91ffe4" strokeWidth="6" strokeLinecap="round"/>
    <path d="M109 113q11 11 22 0" stroke="#91ffe4" strokeWidth="4" strokeLinecap="round"/>
    <ellipse cx="86" cy="110" rx="5" ry="2.5" fill="#4caeb4"/><ellipse cx="154" cy="110" rx="5" ry="2.5" fill="#4caeb4"/>
  </svg>
}

