export type ServiceWorkerRegistrar = (options: {
  immediate: boolean;
  onNeedRefresh: () => void;
}) => (reloadPage?: boolean) => Promise<void>;

export function enablePhonePwaUpdates(register: ServiceWorkerRegistrar): void {
  let activateUpdate: ReturnType<ServiceWorkerRegistrar> | null = null;
  activateUpdate = register({
    immediate: true,
    onNeedRefresh: () => void activateUpdate?.(true),
  });
}
