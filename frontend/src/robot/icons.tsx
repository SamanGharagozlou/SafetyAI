// Small inline icon set (24x24, currentColor). Decorative: always aria-hidden, the text next to them carries the meaning.
interface P { className?: string }
const base = { width: 24, height: 24, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 1.8, strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const, 'aria-hidden': true }

export const PinIcon = ({ className }: P) => <svg className={className} {...base}><path d="M12 21s-7-5.2-7-11a7 7 0 0 1 14 0c0 5.8-7 11-7 11Z"/><circle cx="12" cy="10" r="2.6"/></svg>
export const HomeIcon = ({ className }: P) => <svg className={className} {...base}><path d="M4 11.2 12 4l8 7.2V19a1 1 0 0 1-1 1h-4.2v-5.6H9.2V20H5a1 1 0 0 1-1-1v-7.8Z"/></svg>
export const SendIcon = ({ className }: P) => <svg className={className} {...base}><path d="M21 3 10.4 13.6M21 3l-6.8 18-3.8-7.4L3 9.8 21 3Z"/></svg>
export const StopIcon = ({ className }: P) => <svg className={className} {...base} fill="currentColor" stroke="none"><rect x="6" y="6" width="12" height="12" rx="3"/></svg>
export const CheckIcon = ({ className }: P) => <svg className={className} {...base} strokeWidth="2.4"><path d="m5 12.5 4.6 4.6L19 7.5"/></svg>
export const WarnIcon = ({ className }: P) => <svg className={className} {...base}><path d="M12 4 2.8 20h18.4L12 4Z"/><path d="M12 10v4.2M12 17.4v.1"/></svg>
export const ChatIcon = ({ className }: P) => <svg className={className} {...base}><path d="M4 5.5h16v11H9.4L5 20.2V16.5H4v-11Z"/></svg>
export const SparkIcon = ({ className }: P) => <svg className={className} {...base}><path d="M12 3.5 13.9 9l5.6 1.9-5.6 1.9L12 18.3 10.1 12.8 4.5 10.9 10.1 9 12 3.5Z"/></svg>
export const WalkIcon = ({ className }: P) => <svg className={className} {...base}><circle cx="12" cy="5" r="1.8"/><path d="m9 21 2-6-2.4-2.2L9.8 8.5l3.7 1.4 2.2 2.8M11 15l3.4 1.6L15 21"/></svg>
