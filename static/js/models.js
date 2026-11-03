export function createModelSelectors({ config, ui, document = globalThis.document }) {
  const { $ } = ui;
  function syncModelChoices(modelId, qualityId, preferredModel, preferredQuality) {
    const models = config.models || [];
    if (!models.length) return;
    const modelSelector = $(modelId),
      qualitySelector = $(qualityId);
    const value = modelSelector.value || preferredModel;
    modelSelector.replaceChildren();
    for (const model of models) {
      const opt = document.createElement('option');
      opt.value = model.id;
      opt.textContent = model.label || model.id;
      modelSelector.append(opt);
    }
    modelSelector.value = models.some((m) => m.id === value)
      ? value
      : models.some((m) => m.id === preferredModel)
        ? preferredModel
        : models[0].id;
    const updateQualities = () => {
      const qualities = models.find((m) => m.id === modelSelector.value)?.qualities || [];
      const previous = qualitySelector.value || preferredQuality;
      qualitySelector.replaceChildren();
      for (const quality of qualities) {
        const opt = document.createElement('option');
        opt.value = quality;
        opt.textContent = quality[0].toUpperCase() + quality.slice(1);
        qualitySelector.append(opt);
      }
      qualitySelector.value = qualities.includes(previous)
        ? previous
        : qualities.includes(preferredQuality)
          ? preferredQuality
          : qualities[0] || '';
    };
    modelSelector.onchange = updateQualities;
    updateQualities();
  }
  return { syncModelChoices };
}
