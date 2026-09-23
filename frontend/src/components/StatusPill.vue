<script setup lang="ts">
defineProps<{ status: string }>()
</script>
<template>
  <span class="status" :class="`status-${status.toLowerCase()}`">{{ status.split('_').join(' ') }}</span>
</template>
