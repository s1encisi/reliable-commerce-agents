"use client";

interface ActionChipsProps {
  chips: string[];
  onChipClick: (text: string) => void;
}

/**
 * 快捷操作标签组。
 *
 * 用于在助手回复下方提供一组可点击的追问建议，点击后把该标签文本回填到输入框。
 * 标签文本由调用方（通常来自后端建议）提供，本组件不做内容改写。
 */
export function ActionChips({ chips, onChipClick }: ActionChipsProps) {
  return (
    <div className="flex flex-wrap gap-1.5 mt-2">
      {chips.map((chip) => (
        <button
          key={chip}
          onClick={() => onChipClick(chip)}
          className="rounded-full border border-teal-200 bg-teal-50 px-3 py-1 text-xs font-medium text-teal-700 transition-colors hover:bg-teal-100 hover:border-teal-300"
        >
          {chip}
        </button>
      ))}
    </div>
  );
}
