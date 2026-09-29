import { ref } from 'vue'

/** 明暗主题切换（默认浅色，localStorage 记忆） */
const theme = ref<'light' | 'dark'>(
  document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light',
)

function apply(next: 'light' | 'dark') {
  theme.value = next
  if (next === 'dark') {
    document.documentElement.dataset.theme = 'dark'
    document.documentElement.classList.add('dark')
  } else {
    delete document.documentElement.dataset.theme
    document.documentElement.classList.remove('dark')
  }
  localStorage.setItem('wb-theme', next)
}

export function useTheme() {
  return { theme, toggle: () => apply(theme.value === 'dark' ? 'light' : 'dark') }
}
