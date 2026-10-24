document.addEventListener('DOMContentLoaded', () => {
  const variant = document.querySelector('[data-product-variant]');
  if (variant) {
    const updatePrice = () => {
      const value = Number(variant.selectedOptions[0]?.dataset.price || 0) / 100;
      document.querySelector('#product-price').textContent = new Intl.NumberFormat('uk-UA', { style: 'currency', currency: 'UAH', maximumFractionDigits: value % 1 ? 2 : 0 }).format(value);
    };
    variant.addEventListener('change', updatePrice);
    updatePrice();
  }
  const checkout = document.querySelector('[data-checkout]');
  if (!checkout) return;
  const service = checkout.querySelector('[name=delivery_method]');
  const city = checkout.querySelector('[name=city]');
  const branch = checkout.querySelector('[name=branch]');
  const street = checkout.querySelector('[name=street]');
  const payment = checkout.querySelector('[name=payment_method]');
  let controller;
  const sync = async (refreshBranches = false) => {
    const pickup = service.value === 'pickup';
    const courier = service.value === 'courier';
    city.closest('div').hidden = pickup;
    branch.closest('div').hidden = pickup || courier;
    street.closest('div').hidden = !courier;
    city.required = !pickup;
    branch.required = !pickup && !courier;
    street.required = courier;
    for (const option of payment.options) {
      option.disabled = pickup ? option.value === 'cod' : ['cash', 'card'].includes(option.value);
    }
    if (payment.selectedOptions[0]?.disabled) payment.value = 'online';
    if (refreshBranches && !pickup && !courier) {
      controller?.abort();
      controller = new AbortController();
      const previous = branch.value;
      branch.replaceChildren(new Option('Завантаження…', ''));
      try {
        const response = await fetch(`/shipping/branches/?city=${encodeURIComponent(city.value)}&service=${encodeURIComponent(service.value)}`, { signal: controller.signal });
        if (!response.ok) throw new Error('Directory unavailable');
        const { data } = await response.json();
        branch.replaceChildren(new Option('Оберіть локацію', ''));
        data.forEach(item => branch.add(new Option(item.Description, item.Ref)));
        if ([...branch.options].some(option => option.value === previous)) branch.value = previous;
      } catch (error) {
        if (error.name !== 'AbortError') branch.replaceChildren(new Option('Оновіть сторінку, щоб отримати довідник', ''));
      }
    }
  };
  city.addEventListener('change', () => sync(true));
  service.addEventListener('change', () => sync(true));
  sync(false);
});
